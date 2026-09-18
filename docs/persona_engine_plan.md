# AETHER-I Persona Engine — Implementation Plan

## Architecture Audit Summary

**Project**: AETHER-I (Mark-LIV) — a PyQt6 voice assistant using Gemini Live API  
**Location**: `C:\Users\Toshiba\Downloads\Mark-LIV-main\Mark-LIV-main`  
**Tests**: 460 passing  
**Python**: 3.11.9 via D:\MarkLIV-venv

### Existing Voice System
- 5 Gemini Live voices available: `["Charon", "Puck", "Kore", "Fenrir", "Aoede"]`
- Voice is baked into the Live session at connect time — changing voice requires session reconnect
- Voice selection stored in `config/api_keys.json` as `voice_name`
- `config_manager.py` has `get_voice()` / `save_voice()` with validation against `AVAILABLE_VOICES`
- UI: `CustomizeOverlay` class in `ui.py` with 5 clickable voice pills

### Existing System Prompt
- Built in `main.py` `_build_config()` (lines 955-1034)
- Template: `core/prompt.txt` with placeholders `{assistant_name}`, `{platform}`, `{capabilities}`, `{limits}`, `{tools}`
- Identity context prepended: name, address form
- Memory block injected from `format_memory_for_prompt()`
- Persona will be injected AFTER memory, BEFORE the base prompt

### Existing Tool System
- 3 sources: built-in `TOOL_DECLARATIONS`, `ActionRegistry` (actions/*.py), `PluginRegistry` (plugins/*.py)
- All tools are persona-agnostic — same tools available to all personas
- Authorization via permission system, not persona

### Existing Memory
- `memory/long_term.json` — shared across all personas (identity, preferences, projects, etc.)
- `format_memory_for_prompt()` builds 3 tiers: IDENTITY, RECENT, INDEX

### Existing UI
- Left panel: `_build_left_panel()` with power button, gear button, status indicators
- Quick drawer: `_build_quick_drawer()` with settings overlay buttons
- `CustomizeOverlay` — existing overlay for name/voice/color
- `JarvisUI` wrapper class for thread-safe Qt operations

---

## Voice ↔ Persona Mapping

We have 5 Gemini voices but need 6 personas. Two female personas share the Puck voice:

| Persona | Voice | Rationale |
|---------|-------|-----------|
| 🐺 Fenrir | Fenrir | Exact match — male, authoritative |
| 🌸 Zephyr | Puck | Female, playful — good match for flirty/caring |
| 🔥 Kore | Kore | Exact match — female, playful |
| ⚔️ Charon | Charon | Exact match — male, direct |
| 🎵 Aoede | Aoede | Exact match — female, warm |
| 💎 Leda | Puck | Puck re-used — confident, warm (Zephyr and Leda share voice, personality differentiates) |

---

## Implementation Steps

### Step 1: Persona Data Model (`core/persona.py`)
Create persona schema and profile definitions for all 6 personas.

```python
@dataclass
class PersonaProfile:
    id: str
    name: str
    emoji: str
    gender: str
    voice_id: str
    role: str
    core_traits: str
    communication_style: str
    verbosity: str          # short, medium, long
    humor_level: float      # 0.0-1.0
    warmth: float           # 0.0-1.0
    confidence: float       # 0.0-1.0
    formality: float        # 0.0-1.0
    proactivity: float      # 0.0-1.0
    emotional_expression: float  # 0.0-1.0
    teaching_intensity: float    # 0.0-1.0
    system_prompt: str      # persona-specific additions to the system prompt
    error_tone: str         # how errors are communicated
    greeting: str           # default greeting
```

Six hardcoded profiles at module level: `PERSONA_FENRIR`, `PERSONA_ZEPHYR`, `PERSONA_KORE`, `PERSONA_CHARON`, `PERSONA_AOEDE`, `PERSONA_LEDA`, plus `DEFAULT_PERSONA = PERSONA_CHARON`.

`get_all_personas() -> list[PersonaProfile]` — returns all 6.  
`get_persona(persona_id: str) -> PersonaProfile` — lookup by id.

### Step 2: Persona Registry (`core/persona_registry.py`)
Manages active persona state and switching.

```python
class PersonaRegistry:
    _active: PersonaProfile
    _all: dict[str, PersonaProfile]

    def get_active(self) -> PersonaProfile
    def get_active_id(self) -> str
    def switch_to(self, persona_id: str) -> PersonaProfile  # returns new persona
    def get_voice_for_active(self) -> str  # returns voice name
    def get_system_prompt_addition(self) -> str  # persona-specific prompt text
```

### Step 3: Config Persistence (`memory/config_manager.py`)
Add persona settings:
- `get_active_persona() -> str` — returns persona id (default: `"charon"`)
- `save_active_persona(persona_id: str)` — persists to `api_keys.json`

### Step 4: System Prompt Integration (`main.py`)
In `_build_config()`, inject persona-specific system prompt AFTER memory block, BEFORE base prompt:

```
[ACTIVE PERSONA]
You are currently speaking as {persona.name} ({persona.emoji}).
Role: {persona.role}
Personality: {persona.core_traits}
Communication style: {persona.communication_style}
...
{persona.system_prompt}
```

### Step 5: Voice Integration (`main.py`)
- On persona switch, call `save_voice(persona.voice_id)` then `request_reconnect()`
- Voice change is already handled by the existing reconnect flow

### Step 6: Persona Switching via Chat
Detect persona switch intent in the action dispatch or add a lightweight check before tool dispatch:
- "switch to fenrir", "use kore", "charon mode", etc.
- Call `registry.switch_to(persona_id)`
- Trigger voice change + reconnect
- Return confirmation in the persona's style

### Step 7: UI — Persona Selector Overlay (`ui.py`)
New `PersonaSelectorOverlay` class (similar to `CustomizeOverlay`):
- 6 persona cards with emoji, name, role, traits
- Active/inactive state indicator
- Click to switch
- Accessible from quick drawer

### Step 8: UI — Active Persona Indicator (`ui.py`)
In the left panel or status bar, show current persona name + emoji.

### Step 9: Tests
- Test persona loading, switching, persistence
- Test voice mapping
- Test system prompt injection
- Test shared memory access
- Test shared tool access
- Test error fallback
- Test persona switch during active task
- Regression: run all 460 existing tests

---

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `core/persona.py` | CREATE | PersonaProfile dataclass + 6 profiles |
| `core/persona_registry.py` | CREATE | Active persona state management |
| `memory/config_manager.py` | MODIFY | Add get_active_persona/save_active_persona |
| `main.py` | MODIFY | Inject persona prompt in _build_config(), handle persona switch |
| `core/action_loader.py` | MODIFY | Add persona switching action (or handle in main.py) |
| `ui.py` | MODIFY | Add PersonaSelectorOverlay, active persona indicator |
| `tests/test_persona.py` | CREATE | Persona engine unit tests |
| `tests/test_persona_integration.py` | CREATE | Integration tests |

## Regression Risks

- **System prompt change**: Persona prompt injection could affect LLM behavior for existing users. Mitigate: persona prompt is additive, not replacing existing prompt.
- **Voice change on persona switch**: Session reconnect required. Already handled by existing `_on_voice_change()` flow.
- **Config key collision**: New `active_persona` key must not conflict with existing keys. Low risk — `api_keys.json` has no such key yet.
- **Test isolation**: Persona tests must not depend on network or Gemini API. Use mocking.
