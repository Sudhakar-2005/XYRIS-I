"""Persona Engine — Active persona state management.

Controls which persona is currently active, handles switching,
and provides persona-specific configuration to the rest of the system.
"""

from __future__ import annotations

import logging
from typing import Optional

from core.persona import (
    DEFAULT_PERSONA_ID,
    PersonaProfile,
    get_all_personas,
    get_default_persona,
    get_persona,
    persona_id_exists,
)

logger = logging.getLogger(__name__)


class PersonaRegistry:
    """Manages the active persona for the current session."""

    def __init__(self) -> None:
        self._active: PersonaProfile = get_default_persona()
        self._loaded_from_config: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_active(self) -> PersonaProfile:
        """Return the currently active persona."""
        return self._active

    def get_active_id(self) -> str:
        """Return the id of the currently active persona."""
        return self._active.id

    def get_active_emoji(self) -> str:
        """Return the emoji of the currently active persona."""
        return self._active.emoji

    def get_active_name(self) -> str:
        """Return the name of the currently active persona."""
        return self._active.name

    def get_active_role(self) -> str:
        """Return the role of the currently active persona."""
        return self._active.role

    def get_voice_for_active(self) -> str:
        """Return the Gemini Live voice name for the active persona."""
        return self._active.voice_id

    def get_system_prompt_addition(self) -> str:
        """Build the persona-specific block for the system prompt.

        This is injected AFTER memory and BEFORE the base prompt.
        The intensity level (subtle/balanced/strong) scales how much
        personality expression is requested.
        """
        from memory.config_manager import get_persona_intensity

        p = self._active
        intensity = get_persona_intensity()

        lines = [
            f"[ACTIVE PERSONA — {p.emoji} {p.name.upper()}]",
            f"You are currently speaking as {p.name} ({p.emoji}).",
            f"Role: {p.role}.",
            f"Personality: {p.core_traits}.",
            f"Communication: {p.communication_style}.",
            "",
        ]

        # Intensity modifier
        if intensity == "subtle":
            lines.append(
                "Expression level: SUBTLE. Keep personality hints minimal. "
                "Focus on the task. Let personality show only in word choice "
                "and tone, not in overt behavior."
            )
        elif intensity == "strong":
            lines.append(
                "Expression level: STRONG. Embody this persona fully. "
                "Let personality shine through every response — tone, word "
                "choice, humor, emotion, and style should all reflect who "
                "you are."
            )
        # balanced = no extra modifier (default behavior)

        # Behavioral dimensions — only include non-default values
        dims = []
        if p.warmth > 0.7:
            dims.append("high warmth")
        elif p.warmth < 0.3:
            dims.append("low warmth")
        if p.confidence > 0.8:
            dims.append("high confidence")
        elif p.confidence < 0.3:
            dims.append("low confidence")
        if p.formality > 0.7:
            dims.append("formal")
        elif p.formality < 0.3:
            dims.append("informal")
        if p.humor > 0.7:
            dims.append("use humor naturally")
        elif p.humor < 0.2:
            dims.append("minimal humor")
        if p.playfulness > 0.7:
            dims.append("playful")
        if p.patience > 0.85:
            dims.append("very patient")
        if p.emotional_expression > 0.8:
            dims.append("emotionally expressive")
        elif p.emotional_expression < 0.2:
            dims.append("reserved emotionally")
        if p.teaching_intensity > 0.8:
            dims.append("teach while helping")
        if p.analytical_intensity > 0.8:
            dims.append("analytical approach")

        if dims:
            lines.append("Style: " + ", ".join(dims) + ".")

        if p.response_length_note:
            lines.append(f"Response length: {p.response_length_note}.")

        if p.system_prompt:
            lines.append("")
            lines.append(p.system_prompt)

        if p.error_tone:
            lines.append("")
            lines.append(f"When errors occur: {p.error_tone}")

        lines.append("")
        lines.append(
            "IMPORTANT: This persona affects HOW you communicate, not WHAT "
            "you can do. All tools, memory, permissions, and safety rules "
            "remain identical. Personality changes only presentation."
        )

        return "\n".join(lines)

    def switch_to(self, persona_id: str) -> PersonaProfile:
        """Switch to a different persona by id.

        Returns the new active persona.
        Raises ValueError if the persona id is not valid.
        """
        persona_id = persona_id.lower().strip()

        if not persona_id_exists(persona_id):
            raise ValueError(f"Unknown persona: {persona_id!r}")

        old = self._active
        new = get_persona(persona_id)
        assert new is not None  # guaranteed by persona_id_exists check

        if old.id == new.id:
            logger.debug("Persona already active: %s", new.name)
            return new

        self._active = new
        logger.info(
            "Persona switched: %s (%s) -> %s (%s)",
            old.name, old.id, new.name, new.id,
        )
        return new

    def load_from_config(self, persona_id: Optional[str] = None) -> PersonaProfile:
        """Load persona from config at startup.

        If persona_id is None or invalid, falls back to the default.
        """
        self._loaded_from_config = True

        if persona_id and persona_id_exists(persona_id):
            self._active = get_persona(persona_id)
            logger.info("Loaded persona from config: %s", self._active.name)
        else:
            self._active = get_default_persona()
            if persona_id:
                logger.warning(
                    "Invalid persona id %r from config, using default: %s",
                    persona_id, self._active.name,
                )
            else:
                logger.info("No persona in config, using default: %s", self._active.name)

        return self._active

    def get_confirmation_message(self, old_name: str, new_persona: PersonaProfile) -> str:
        """Generate a natural confirmation message after a persona switch.

        Uses the NEW persona's style to confirm.
        """
        p = new_persona
        if p.id == "fenrir":
            return f"Switched to {p.name}. I will guide you with clarity."
        elif p.id == "zephyr":
            return f"Hey, it is {p.name} now! I am right here with you."
        elif p.id == "kore":
            return f"Kore here! Finally. Took you long enough."
        elif p.id == "charon":
            return f"{p.name} active. Standing by."
        elif p.id == "aoede":
            return f"I am {p.name} now. Let me help you with care."
        elif p.id == "leda":
            return f"Leda here. Confident and ready."
        else:
            return f"Switched to {p.name}."

    @property
    def is_loaded(self) -> bool:
        """Whether load_from_config has been called."""
        return self._loaded_from_config
