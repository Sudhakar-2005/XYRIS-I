"""
Persona Engine — Comprehensive Test Suite.

Tests persona loading, switching, voice mapping, system prompt generation,
persistence, personality isolation, error fallback, and config integration.
"""
import unittest
import sys
import json
import tempfile
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.persona import (
    PersonaProfile,
    ALL_PERSONAS,
    PERSONA_BY_ID,
    DEFAULT_PERSONA_ID,
    get_all_personas,
    get_persona,
    get_default_persona,
    persona_id_exists,
    PERSONA_FENRIR,
    PERSONA_ZEPHYR,
    PERSONA_KORE,
    PERSONA_CHARON,
    PERSONA_AOEDE,
    PERSONA_LEDA,
)
from core.persona_registry import PersonaRegistry


class TestPersonaProfiles(unittest.TestCase):
    """Test persona data model and profile definitions."""

    def test_all_personas_count(self):
        personas = get_all_personas()
        self.assertEqual(len(personas), 6)

    def test_persona_ids_unique(self):
        ids = [p.id for p in ALL_PERSONAS]
        self.assertEqual(len(ids), len(set(ids)))

    def test_persona_names_unique(self):
        names = [p.name for p in ALL_PERSONAS]
        self.assertEqual(len(names), len(set(names)))

    def test_persona_voices_valid(self):
        """All persona voices must be in the allowed Gemini voice set."""
        valid_voices = {"Charon", "Puck", "Kore", "Fenrir", "Aoede"}
        for p in ALL_PERSONAS:
            self.assertIn(p.voice_id, valid_voices,
                          f"{p.name} has invalid voice: {p.voice_id}")

    def test_persona_by_id_lookup(self):
        for p in ALL_PERSONAS:
            found = get_persona(p.id)
            self.assertIsNotNone(found, f"get_persona({p.id!r}) returned None")
            self.assertEqual(found.id, p.id)

    def test_persona_by_id_invalid(self):
        self.assertIsNone(get_persona("nonexistent"))

    def test_persona_id_exists(self):
        for p in ALL_PERSONAS:
            self.assertTrue(persona_id_exists(p.id))
        self.assertFalse(persona_id_exists("nonexistent"))

    def test_default_persona_is_charon(self):
        default = get_default_persona()
        self.assertEqual(default.id, DEFAULT_PERSONA_ID)
        self.assertEqual(default.id, "charon")

    def test_fenrir_profile(self):
        p = PERSONA_FENRIR
        self.assertEqual(p.id, "fenrir")
        self.assertEqual(p.name, "Fenrir")
        self.assertEqual(p.gender, "Male")
        self.assertEqual(p.voice_id, "Fenrir")
        self.assertEqual(p.role, "Teacher / Mentor")
        self.assertIn("wise", p.core_traits.lower())
        self.assertGreater(p.teaching_intensity, 0.8)
        self.assertGreater(p.formality, 0.5)

    def test_zephyr_profile(self):
        p = PERSONA_ZEPHYR
        self.assertEqual(p.id, "zephyr")
        self.assertEqual(p.name, "Zephyr")
        self.assertEqual(p.gender, "Female")
        self.assertEqual(p.voice_id, "Puck")
        self.assertEqual(p.role, "Romantic Companion")
        self.assertIn("flirty", p.core_traits.lower())
        self.assertGreater(p.warmth, 0.8)
        self.assertGreater(p.emotional_expression, 0.8)

    def test_kore_profile(self):
        p = PERSONA_KORE
        self.assertEqual(p.id, "kore")
        self.assertEqual(p.name, "Kore")
        self.assertEqual(p.gender, "Female")
        self.assertEqual(p.voice_id, "Kore")
        self.assertEqual(p.role, "Best Friend")
        self.assertIn("playful", p.core_traits.lower())
        self.assertGreater(p.humor, 0.7)
        self.assertGreater(p.playfulness, 0.8)

    def test_charon_profile(self):
        p = PERSONA_CHARON
        self.assertEqual(p.id, "charon")
        self.assertEqual(p.name, "Charon")
        self.assertEqual(p.gender, "Male")
        self.assertEqual(p.voice_id, "Charon")
        self.assertEqual(p.role, "Devoted Assistant / Executor")
        self.assertIn("loyal", p.core_traits.lower())
        self.assertGreater(p.directness, 0.8)
        self.assertLess(p.verbosity, 0.4)

    def test_aoede_profile(self):
        p = PERSONA_AOEDE
        self.assertEqual(p.id, "aoede")
        self.assertEqual(p.name, "Aoede")
        self.assertEqual(p.gender, "Female")
        self.assertEqual(p.voice_id, "Aoede")
        self.assertEqual(p.role, "Nurturing Mentor / Companion")
        self.assertIn("caring", p.core_traits.lower())
        self.assertGreater(p.patience, 0.85)

    def test_leda_profile(self):
        p = PERSONA_LEDA
        self.assertEqual(p.id, "leda")
        self.assertEqual(p.name, "Leda")
        self.assertEqual(p.gender, "Female")
        self.assertEqual(p.voice_id, "Puck")
        self.assertEqual(p.role, "Confident Romantic Companion")
        self.assertIn("confident", p.core_traits.lower())
        self.assertGreater(p.confidence, 0.8)

    def test_all_personas_have_system_prompt(self):
        for p in ALL_PERSONAS:
            self.assertTrue(p.system_prompt,
                            f"{p.name} missing system_prompt")
            self.assertTrue(len(p.system_prompt) > 20,
                            f"{p.name} system_prompt too short")

    def test_all_personas_have_greeting(self):
        for p in ALL_PERSONAS:
            self.assertTrue(p.greeting,
                            f"{p.name} missing greeting")

    def test_all_personas_have_error_tone(self):
        for p in ALL_PERSONAS:
            self.assertTrue(p.error_tone,
                            f"{p.name} missing error_tone")

    def test_all_personas_have_role(self):
        for p in ALL_PERSONAS:
            self.assertTrue(p.role,
                            f"{p.name} missing role")

    def test_persona_profiles_are_frozen(self):
        """PersonaProfile is frozen — no mutation allowed."""
        p = PERSONA_FENRIR
        with self.assertRaises(AttributeError):
            p.name = "Modified"


class TestPersonaRegistry(unittest.TestCase):
    """Test persona state management and switching."""

    def setUp(self):
        self.registry = PersonaRegistry()

    def test_default_active_persona(self):
        active = self.registry.get_active()
        self.assertEqual(active.id, "charon")
        self.assertEqual(active.name, "Charon")

    def test_get_active_id(self):
        self.assertEqual(self.registry.get_active_id(), "charon")

    def test_get_active_emoji(self):
        self.assertEqual(self.registry.get_active_emoji(), "⚔️")

    def test_get_active_name(self):
        self.assertEqual(self.registry.get_active_name(), "Charon")

    def test_get_voice_for_active(self):
        self.assertEqual(self.registry.get_voice_for_active(), "Charon")

    def test_switch_persona(self):
        new = self.registry.switch_to("fenrir")
        self.assertEqual(new.id, "fenrir")
        self.assertEqual(self.registry.get_active_id(), "fenrir")
        self.assertEqual(self.registry.get_voice_for_active(), "Fenrir")

    def test_switch_to_all_personas(self):
        for p in ALL_PERSONAS:
            self.registry.switch_to(p.id)
            self.assertEqual(self.registry.get_active_id(), p.id)
            self.assertEqual(self.registry.get_voice_for_active(), p.voice_id)

    def test_switch_to_invalid_raises(self):
        with self.assertRaises(ValueError):
            self.registry.switch_to("nonexistent")

    def test_switch_to_same_persona(self):
        """Switching to the same persona is a no-op."""
        initial = self.registry.get_active()
        result = self.registry.switch_to("charon")
        self.assertEqual(result.id, initial.id)

    def test_load_from_config_valid(self):
        self.registry.load_from_config("fenrir")
        self.assertEqual(self.registry.get_active_id(), "fenrir")

    def test_load_from_config_invalid_falls_back(self):
        self.registry.load_from_config("nonexistent")
        self.assertEqual(self.registry.get_active_id(), "charon")

    def test_load_from_config_none_falls_back(self):
        self.registry.load_from_config(None)
        self.assertEqual(self.registry.get_active_id(), "charon")

    def test_is_loaded(self):
        self.assertFalse(self.registry.is_loaded)
        self.registry.load_from_config("fenrir")
        self.assertTrue(self.registry.is_loaded)


class TestPersonaSystemPrompt(unittest.TestCase):
    """Test persona system prompt generation."""

    def setUp(self):
        self.registry = PersonaRegistry()

    def test_prompt_contains_persona_header(self):
        self.registry.switch_to("fenrir")
        prompt = self.registry.get_system_prompt_addition()
        self.assertIn("[ACTIVE PERSONA", prompt)
        self.assertIn("FENRIR", prompt)

    def test_prompt_contains_role(self):
        self.registry.switch_to("fenrir")
        prompt = self.registry.get_system_prompt_addition()
        self.assertIn("Teacher / Mentor", prompt)

    def test_prompt_contains_personality(self):
        self.registry.switch_to("fenrir")
        prompt = self.registry.get_system_prompt_addition()
        self.assertIn("Wise, respectful, guiding", prompt)

    def test_prompt_contains_system_prompt(self):
        self.registry.switch_to("fenrir")
        prompt = self.registry.get_system_prompt_addition()
        self.assertIn("mentor", prompt.lower())

    def test_prompt_contains_error_tone(self):
        self.registry.switch_to("fenrir")
        prompt = self.registry.get_system_prompt_addition()
        self.assertIn("error", prompt.lower())

    def test_prompt_contains_safety_notice(self):
        """All persona prompts must contain the safety disclaimer."""
        for p in ALL_PERSONAS:
            self.registry.switch_to(p.id)
            prompt = self.registry.get_system_prompt_addition()
            self.assertIn("All tools, memory, permissions", prompt,
                          f"{p.name} prompt missing safety notice")

    def test_prompt_differs_per_persona(self):
        """Each persona should produce a different system prompt."""
        prompts = set()
        for p in ALL_PERSONAS:
            self.registry.switch_to(p.id)
            prompts.add(self.registry.get_system_prompt_addition())
        self.assertEqual(len(prompts), 6, "Not all personas produce unique prompts")


class TestPersonaVoiceMapping(unittest.TestCase):
    """Test voice mapping for each persona."""

    def setUp(self):
        self.registry = PersonaRegistry()

    def test_fenrir_voice(self):
        self.registry.switch_to("fenrir")
        self.assertEqual(self.registry.get_voice_for_active(), "Fenrir")

    def test_zephyr_voice(self):
        self.registry.switch_to("zephyr")
        self.assertEqual(self.registry.get_voice_for_active(), "Puck")

    def test_kore_voice(self):
        self.registry.switch_to("kore")
        self.assertEqual(self.registry.get_voice_for_active(), "Kore")

    def test_charon_voice(self):
        self.registry.switch_to("charon")
        self.assertEqual(self.registry.get_voice_for_active(), "Charon")

    def test_aoede_voice(self):
        self.registry.switch_to("aoede")
        self.assertEqual(self.registry.get_voice_for_active(), "Aoede")

    def test_leda_voice(self):
        self.registry.switch_to("leda")
        self.assertEqual(self.registry.get_voice_for_active(), "Puck")


class TestPersonaConfirmationMessages(unittest.TestCase):
    """Test persona switch confirmation messages."""

    def setUp(self):
        self.registry = PersonaRegistry()

    def test_all_personas_have_confirmation(self):
        for p in ALL_PERSONAS:
            msg = self.registry.get_confirmation_message("Old", p)
            self.assertTrue(len(msg) > 5,
                            f"{p.name} has empty confirmation message")

    def test_confirmation_uses_persona_name(self):
        for p in ALL_PERSONAS:
            msg = self.registry.get_confirmation_message("Old", p)
            self.assertIn(p.name, msg,
                          f"{p.name} confirmation doesn't mention name")


class TestPersonaConfigPersistence(unittest.TestCase):
    """Test persona config persistence."""

    def test_get_active_persona_default(self):
        from memory.config_manager import get_active_persona
        with patch("memory.config_manager.load_api_keys", return_value={}):
            result = get_active_persona()
            self.assertEqual(result, "charon")

    def test_get_active_persona_saved(self):
        from memory.config_manager import get_active_persona
        with patch("memory.config_manager.load_api_keys",
                    return_value={"active_persona": "fenrir"}):
            result = get_active_persona()
            self.assertEqual(result, "fenrir")

    def test_save_active_persona(self):
        from memory.config_manager import save_active_persona
        mock_data = {}
        with patch("memory.config_manager.CONFIG_FILE") as mock_file:
            mock_file.exists.return_value = True
            mock_file.read_text.return_value = json.dumps(mock_data)
            mock_file.write_text = lambda text, **kw: mock_data.update(
                json.loads(text)
            )
            save_active_persona("fenrir")
            self.assertEqual(mock_data.get("active_persona"), "fenrir")


class TestPersonaIsolation(unittest.TestCase):
    """Test that personas don't bleed into each other."""

    def setUp(self):
        self.registry = PersonaRegistry()

    def test_switching_preserves_nothing_between_personas(self):
        """Switching personas should fully replace the active persona."""
        self.registry.switch_to("fenrir")
        fenrir_prompt = self.registry.get_system_prompt_addition()
        fenrir_voice = self.registry.get_voice_for_active()

        self.registry.switch_to("kore")
        kore_prompt = self.registry.get_system_prompt_addition()
        kore_voice = self.registry.get_voice_for_active()

        self.assertNotEqual(fenrir_prompt, kore_prompt)
        self.assertNotEqual(fenrir_voice, kore_voice)
        self.assertEqual(self.registry.get_active_id(), "kore")

    def test_no_personality_leak_fenrir_to_kore(self):
        """Fenrir traits should not appear in Kore's prompt."""
        self.registry.switch_to("kore")
        prompt = self.registry.get_system_prompt_addition()
        self.assertNotIn("Teacher / Mentor", prompt)
        self.assertIn("Best Friend", prompt)

    def test_no_personality_leak_charon_to_zephyr(self):
        """Charon traits should not appear in Zephyr's prompt."""
        self.registry.switch_to("zephyr")
        prompt = self.registry.get_system_prompt_addition()
        self.assertNotIn("Devoted Assistant", prompt)
        self.assertIn("Romantic Companion", prompt)


class TestPersonaDetection(unittest.TestCase):
    """Test persona switch detection in user text."""

    def setUp(self):
        self.registry = PersonaRegistry()
        # We'll test the detection logic directly
        from core.persona import PERSONA_BY_ID
        self._by_id = PERSONA_BY_ID

    def _detect(self, text: str) -> str | None:
        """Replicate the detection logic from main.py."""
        import re
        _text = text.strip().lower()
        if _text in self._by_id:
            return _text
        patterns = [
            r"switch(?:\s+to)\s+(fenrir|zephyr|kore|charon|aoede|leda)",
            r"use\s+(fenrir|zephyr|kore|charon|aoede|leda)",
            r"(fenrir|zephyr|kore|charon|aoede|leda)\s+mode",
            r"i\s+want\s+(fenrir|zephyr|kore|charon|aoede|leda)",
            r"activate\s+(fenrir|zephyr|kore|charon|aoede|leda)",
            r"be\s+(fenrir|zephyr|kore|charon|aoede|leda)",
            r"change\s+to\s+(fenrir|zephyr|kore|charon|aoede|leda)",
        ]
        for pat in patterns:
            m = re.search(pat, _text)
            if m:
                return m.group(1)
        return None

    def test_direct_name(self):
        for pid in self._by_id:
            self.assertEqual(self._detect(pid), pid)

    def test_switch_to(self):
        self.assertEqual(self._detect("switch to fenrir"), "fenrir")
        self.assertEqual(self._detect("Switch to KORE"), "kore")

    def test_use(self):
        self.assertEqual(self._detect("use zephyr"), "zephyr")

    def test_mode(self):
        self.assertEqual(self._detect("charon mode"), "charon")
        self.assertEqual(self._detect("AOEDE mode"), "aoede")

    def test_i_want(self):
        self.assertEqual(self._detect("i want leda"), "leda")

    def test_activate(self):
        self.assertEqual(self._detect("activate fenrir"), "fenrir")

    def test_be(self):
        self.assertEqual(self._detect("be kore"), "kore")

    def test_change_to(self):
        self.assertEqual(self._detect("change to aoede"), "aoede")

    def test_no_match(self):
        self.assertIsNone(self._detect("hello there"))
        self.assertIsNone(self._detect("what is the weather"))
        self.assertIsNone(self._detect("open paint"))


class TestSharedMemory(unittest.TestCase):
    """All personas share the same memory."""

    def test_all_personas_access_same_memory(self):
        """This is architectural — persona doesn't affect memory access."""
        from memory.memory_manager import load_memory
        memory = load_memory()
        self.assertIsInstance(memory, dict)
        # All personas would get the same memory
        for p in ALL_PERSONAS:
            m = load_memory()
            self.assertEqual(m, memory)


class TestErrorFallback(unittest.TestCase):
    """Persona Engine error handling."""

    def test_invalid_persona_id_doesnt_crash(self):
        registry = PersonaRegistry()
        with self.assertRaises(ValueError):
            registry.switch_to("nonexistent")
        # Registry should still be on previous persona
        self.assertEqual(registry.get_active_id(), "charon")

    def test_registry_works_after_failed_switch(self):
        registry = PersonaRegistry()
        try:
            registry.switch_to("nonexistent")
        except ValueError:
            pass
        # Should still work
        new = registry.switch_to("fenrir")
        self.assertEqual(new.id, "fenrir")


class TestPersonaDimensions(unittest.TestCase):
    """Verify persona behavioral dimensions are within valid range."""

    def test_all_dimensions_in_range(self):
        for p in ALL_PERSONAS:
            for dim_name in ["warmth", "confidence", "formality", "humor",
                             "playfulness", "verbosity", "directness",
                             "patience", "proactivity", "emotional_expression",
                             "teaching_intensity", "analytical_intensity"]:
                val = getattr(p, dim_name)
                self.assertGreaterEqual(val, 0.0,
                    f"{p.name}.{dim_name} = {val} < 0.0")
                self.assertLessEqual(val, 1.0,
                    f"{p.name}.{dim_name} = {val} > 1.0")


class TestPersonaIntensity(unittest.TestCase):
    """Test persona intensity settings."""

    def test_intensity_config_default(self):
        from memory.config_manager import get_persona_intensity
        with patch("memory.config_manager.load_api_keys", return_value={}):
            self.assertEqual(get_persona_intensity(), "balanced")

    def test_intensity_config_valid(self):
        from memory.config_manager import get_persona_intensity
        for level in ("subtle", "balanced", "strong"):
            with patch("memory.config_manager.load_api_keys",
                        return_value={"persona_intensity": level}):
                self.assertEqual(get_persona_intensity(), level)

    def test_intensity_config_invalid_falls_back(self):
        from memory.config_manager import get_persona_intensity
        with patch("memory.config_manager.load_api_keys",
                    return_value={"persona_intensity": "invalid"}):
            self.assertEqual(get_persona_intensity(), "balanced")

    def test_intensity_prompt_subtle(self):
        registry = PersonaRegistry()
        registry.switch_to("kore")
        with patch("memory.config_manager.get_persona_intensity",
                    return_value="subtle"):
            prompt = registry.get_system_prompt_addition()
            self.assertIn("SUBTLE", prompt)
            self.assertIn("minimal", prompt.lower())

    def test_intensity_prompt_strong(self):
        registry = PersonaRegistry()
        registry.switch_to("kore")
        with patch("memory.config_manager.get_persona_intensity",
                    return_value="strong"):
            prompt = registry.get_system_prompt_addition()
            self.assertIn("STRONG", prompt)
            self.assertIn("Embody", prompt)

    def test_intensity_prompt_balanced(self):
        registry = PersonaRegistry()
        registry.switch_to("kore")
        with patch("memory.config_manager.get_persona_intensity",
                    return_value="balanced"):
            prompt = registry.get_system_prompt_addition()
            # Balanced should NOT contain the intensity modifiers
            self.assertNotIn("SUBTLE", prompt)
            self.assertNotIn("STRONG", prompt)


class TestVoicePersonaSwitch(unittest.TestCase):
    """Test voice-based persona switch detection."""

    def setUp(self):
        self.registry = PersonaRegistry()
        from core.persona import PERSONA_BY_ID
        self._by_id = PERSONA_BY_ID

    def _detect(self, text: str) -> str | None:
        """Replicate the detection logic from main.py."""
        import re
        _text = text.strip().lower()
        if _text in self._by_id:
            return _text
        patterns = [
            r"switch(?:\s+to)\s+(fenrir|zephyr|kore|charon|aoede|leda)",
            r"use\s+(fenrir|zephyr|kore|charon|aoede|leda)",
            r"(fenrir|zephyr|kore|charon|aoede|leda)\s+mode",
            r"i\s+want\s+(fenrir|zephyr|kore|charon|aoede|leda)",
            r"activate\s+(fenrir|zephyr|kore|charon|aoede|leda)",
            r"be\s+(fenrir|zephyr|kore|charon|aoede|leda)",
            r"change\s+to\s+(fenrir|zephyr|kore|charon|aoede|leda)",
        ]
        for pat in patterns:
            m = re.search(pat, _text)
            if m:
                return m.group(1)
        return None

    def test_natural_voice_phrases(self):
        """Test realistic voice commands."""
        self.assertEqual(self._detect("switch to fenrir"), "fenrir")
        self.assertEqual(self._detect("use kore please"), "kore")
        self.assertEqual(self._detect("charon mode"), "charon")
        self.assertEqual(self._detect("I want Aoede"), "aoede")
        self.assertEqual(self._detect("activate Leda"), "leda")

    def test_ambient_speech_not_detected(self):
        """Normal conversation should not trigger persona switch."""
        self.assertIsNone(self._detect("what is the weather today"))
        self.assertIsNone(self._detect("open paint for me"))
        self.assertIsNone(self._detect("how are you doing"))
        self.assertIsNone(self._detect("search my files"))
        self.assertIsNone(self._detect("send an email"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
