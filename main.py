import platform as _platform
import subprocess as _subprocess

# ── Nuclear: force CREATE_NO_WINDOW on EVERY subprocess call on Windows ───────
# This patches Popen itself, so no per-file flag is needed anywhere.
if _platform.system() == "Windows":
    _OrigPopen = _subprocess.Popen

    class _Popen(_OrigPopen):
        def __init__(self, args, **kw):
            kw["creationflags"] = kw.get("creationflags", 0) | _subprocess.CREATE_NO_WINDOW
            kw.pop("startupinfo", None)   # drop any stale/shared STARTUPINFO
            super().__init__(args, **                       kw)

    _subprocess.Popen = _Popen


# ── Console must survive non-UTF-8 code pages ────────────────────────────────
# Every status line in this file carries an emoji, and on a legacy Windows
# console the active code page is the system one — cp1254 in Turkey, cp1251 in
# Russia, cp932 in Japan. Printing an emoji there raises UnicodeEncodeError, and
# because most of these prints sit inside the receive loop it takes the session
# down on startup. Reconfiguring to UTF-8 with a replacement fallback costs
# nothing and makes the app launch the same way in every locale.
import sys as _sys

for _stream in ("stdout", "stderr"):
    try:
        _s = getattr(_sys, _stream, None)
        if _s is not None and hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass          # pythonw / redirected pipes / anything exotic — never fatal

# ─────────────────────────────────────────────────────────────────────────────

# Startup instrumentation (§3.19): dependency-free stopwatch imported BEFORE
# the heavy imports it measures. Marks are always recorded; the report prints
# only with AETHER_PERF=1 (see core/perf.py, tools/bench_startup.py).
from core.perf import mark as _perf_mark, print_report_if_enabled as _perf_report

import asyncio
import logging
import re
import threading
import time
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

import sounddevice as sd
import numpy as np
_perf_mark("import:sounddevice+numpy")
from google import genai
from google.genai import types
_perf_mark("import:google-genai")
from ui import JarvisUI
_perf_mark("import:ui")
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
    save_session_summary, pop_last_session,
    search_memory, set_trim_notifier,
    save_session_log, get_saved_sessions,
)

# The file-backed tools (open_app, web_search, browser_control, …) are no longer
# imported or declared here — they self-describe via a TOOL dict in their own
# actions/*.py file and are auto-discovered by core.action_loader at startup.
# Only tools that are tied to live-session state stay inline in this file
# (screen_process, close_camera, save_memory, manage_monitor, shutdown_jarvis,
# system_status).
from actions.screen_processor  import _capture_camera, _capture_screen
from actions.system_monitor    import SystemMonitor, get_system_status
from actions.proactive         import ProactiveEngine
from actions.background_monitor import (
    add_monitor, remove_monitor, list_monitors, check_all as monitor_check_all,
)
from actions.web_search        import _news as _fetch_news_sync
from memory.config_manager     import (
    get_brief_enabled, get_media_resolution, get_proactive_audio_enabled,
    get_push_to_talk_enabled, get_thinking_enabled, get_turn_tuning, get_voice,
    get_wake_word_enabled, save_wake_word_enabled,    get_input_device, get_output_device,
)
from core.plugin_loader        import discover_plugins
from core                      import undo as undo_stack
from core                      import confirm as confirm_gate
from core                      import audio_devices
from core.action_loader        import discover_actions, ToolPolicy, policy_from_meta
from core.echo                 import EchoGuard
from core.viseme               import VisemeStream
from core.vision_labels        import CAMERA, SCREEN, source_text
from core.event_bus            import EventBus, Event, EventType, Priority
from core.permissions          import PermissionLedger
from core.gates                import evaluate_gates, message_for
from core.activity             import ActivityFeed
from core.session_memory       import SessionMemory
from core.degraded             import EndpointDetector, OfflineVoice
from core.verifier import (
    Verifier, VerificationRule, VerificationStrategy,
    VerificationResult, VerificationStatus, verdict_dict,
)
from core.wake_word            import (
    WakeWordDetector, is_ready as wake_is_ready, install_and_download as wake_install,
)
_perf_mark("import:actions+memory+core")

# How long the assistant stays awake with no user speech before it auto-sleeps
# again (wake-word mode only).
WAKE_SLEEP_TIMEOUT = 120.0   # seconds (2 minutes)

def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"
# One definition of the Live model string, shared with gemini.py's one-shot
# side calls — change it in core/model_manager.py only.
from core.model_manager import LIVE_MODEL
_perf_mark("imports_done")
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000 
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 1024

# RMS below which 16-bit PCM is treated as room silence; above _LEVEL_FULL it
# reads as a full-height waveform. Tuned so ordinary speech lands mid-range and
# the bars still move for a quiet talker — language- and device-independent.
_LEVEL_FLOOR = 60.0
_LEVEL_FULL  = 2600.0


def _pcm_level(samples) -> float:
    """Map a block of int16 PCM samples to a 0.0–1.0 loudness level for the HUD
    waveform. Returns 0.0 on empty/invalid input so it can never raise."""
    try:
        x = np.asarray(samples, dtype=np.float32)
        if x.size == 0:
            return 0.0
        rms = float(np.sqrt(np.mean(x * x)))
    except Exception:
        return 0.0
    if rms <= _LEVEL_FLOOR:
        return 0.0
    return min(1.0, (rms - _LEVEL_FLOOR) / (_LEVEL_FULL - _LEVEL_FLOOR))


# ── Viseme extraction ─────────────────────────────────────────────────────────
# The avatar's mouth used to be driven by one RMS value per ~200 ms write batch,
# which is five updates a second averaged over a fifth of a second — it could
# only ever flap. These read the *shape* of each 20 ms slice straight from the
# spectrum of the audio being played, so no transcript, no forced alignment and
# no language assumption: it works the same for Turkish and English.
#
# Two numbers come out. Openness tracks the first formant — F1 climbs as the jaw
# drops, so /a/ reads open and /i/ or /u/ read closed. Width tracks the second —
# F2 is high for spread vowels (/i/, /e/) and low for rounded ones (/u/, /o/).
# Extra time beyond the device's reported output latency before the microphone
# is trusted again: covers room decay and the speaker's own settling.
_TAIL_MARGIN = 0.25

_VIS_WIN = 1024        # ~43 ms analysis window at 24 kHz: enough for formants
_VIS_HOP = 480         # 20 ms between frames, i.e. 50 shapes a second

# Delay from handing the first bytes of a reply to an already-running output
# stream to hearing them: one callback period, plus whatever the DAC adds.
_FIRST_SOUND = CHUNK_SIZE / RECEIVE_SAMPLE_RATE      # ~43 ms
# How far past the device's own buffer the mouth's timeline may drift before it
# is re-anchored. The buffer is the hard limit on how much audio can be queued
# ahead, so anything beyond it plus a margin for clock error is impossible.
_CURSOR_SLACK = 0.15

# Erring early is the safe direction. A viewer tolerates a mouth that moves
# slightly before the sound far better than one that moves after it — the
# broadcast limits are about 45 ms of lag against 125 ms of lead — so where
# this is uncertain it is biased to lead.


def _pcm_visemes(samples, sr: int = 24000):
    """Slice a PCM block into (level, openness, width) frames, one per 20 ms.

    Returns [] on anything unexpected — the mouth falls back to loudness-only
    articulation rather than the caller having to handle an error.
    """
    try:
        x = np.asarray(samples, dtype=np.float32)
        if x.size < _VIS_WIN:
            return []
        win = np.hanning(_VIS_WIN).astype(np.float32)
        freqs = np.fft.rfftfreq(_VIS_WIN, 1.0 / sr)
        b_f1_lo = (freqs >= 150) & (freqs < 450)     # F1 of close vowels
        b_f1_hi = (freqs >= 450) & (freqs < 1100)    # F1 of open vowels
        b_f2_bk = (freqs >= 600) & (freqs < 1300)    # F2 of rounded vowels
        b_f2_fr = (freqs >= 1700) & (freqs < 3200)   # F2 of spread vowels
        b_hiss = (freqs >= 3800) & (freqs < 8000)    # fricatives

        # One frame per hop across the *whole* block. Stepping only while a full
        # window fits stopped 1024 - 480 samples short of the end, so a 200 ms
        # batch yielded 160 ms of schedule: the mouth ran out of frames before
        # the audio ran out of sound, and each batch no longer lined up with the
        # end of the one before it. Losing 20 % of every batch is most of why
        # the mouth did not track the words.
        out = []
        for start in range(0, x.size, _VIS_HOP):
            # The level gates closures, so it is measured over exactly this
            # 20 ms and never looks ahead. The spectrum needs a longer window
            # to resolve formants and may be short-filled at the very end.
            level = _pcm_level(x[start:start + _VIS_HOP])
            seg = x[start:start + _VIS_WIN]
            if seg.size < _VIS_WIN:
                seg = np.concatenate([seg, np.zeros(_VIS_WIN - seg.size,
                                                    dtype=np.float32)])
            if level <= 0.0:
                out.append((0.0, 0.0, 0.0))
                continue
            mag = np.abs(np.fft.rfft((seg - seg.mean()) * win))
            f1l, f1h = float(mag[b_f1_lo].sum()), float(mag[b_f1_hi].sum())
            f2b, f2f = float(mag[b_f2_bk].sum()), float(mag[b_f2_fr].sum())
            hiss = float(mag[b_hiss].sum())

            openness = f1h / (f1l + f1h + 1e-6)
            width = (f2f - f2b) / (f2f + f2b + 1e-6)
            # A wide-open jaw physically cannot purse, so openness damps width.
            # /a/ has a low enough F2 to read as "rounded" on the bands alone;
            # letting openness suppress the width term is what keeps an open
            # vowel from pursing.
            width *= (1.0 - openness) ** 0.8
            # Fricatives are formed with a nearly closed mouth.
            h = hiss / (f1l + f1h + f2b + f2f + hiss + 1e-6)
            openness *= 1.0 - 0.65 * min(1.0, h * 2.5)
            out.append((level,
                        float(min(1.0, max(0.0, openness))),
                        float(min(1.0, max(-1.0, width)))))
        return out
    except Exception:
        return []


def _describe_tools(declarations) -> str:
    """One line per capability, straight from the live tool declarations.

    Derived rather than written down: the action and plugin registries are
    discovered at startup, so whatever the user has installed is what the model
    is told it can do. Adding a plugin extends this by itself, and removing one
    stops the model from claiming an ability it no longer has.
    """
    lines = []
    for d in declarations or ():
        try:
            name = d.get("name") if isinstance(d, dict) else getattr(d, "name", None)
            desc = (d.get("description") if isinstance(d, dict)
                    else getattr(d, "description", "")) or ""
        except Exception:
            continue
        if not name:
            continue
        desc = " ".join(str(desc).split())
        lines.append(f"- {name}: {desc[:150]}" if desc else f"- {name}")
    return "\n".join(lines)


def _describe_limits(has_vision: bool, has_mic: bool) -> str:
    """The other half of self-knowledge: what is out of reach, and why.

    Derived from how the program is actually built, not from a list of refusals.
    A model that knows its boundaries stops improvising around them, and stating
    them as architecture rather than as rules keeps the answer honest in any
    language.
    """
    out = [
        "- Anything not listed above is outside your reach. Say so in one clause "
        "and offer the nearest thing you can actually do — never mime an action "
        "you cannot take, and never report a result you did not get.",
        "- You act on this machine only. You cannot reach the user's other "
        "devices, accounts or hardware except through the tools listed above.",
        "- You remember what is in the memory block and what has been said this "
        "session. Anything else you were told before is gone unless it was saved.",
    ]
    if has_vision:
        out.append(
            "- Your sight is not continuous. You see nothing until you call a "
            "vision tool, and then only that single frame at that moment — you "
            "cannot watch, monitor or notice something changing on screen.")
    else:
        out.append("- You have no sight at all in this build.")
    if has_mic:
        out.append(
            "- You hear nothing while the microphone is muted, and you cannot "
            "unmute it yourself.")
    return "\n".join(out)


def _render_prompt(template: str, values: dict) -> str:
    """Fill {tokens} in the prompt template.

    A plain replace rather than str.format: the file is meant to be edited by
    hand, and a stray brace in someone's own wording must never take the app
    down at startup.
    """
    out = template or ""
    for key, val in values.items():
        out = out.replace("{" + key + "}", str(val))
    return out


def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are JARVIS, Tony Stark's AI assistant. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool."
        )

_CTRL_RE = re.compile(r"<ctrl\d+>", re.IGNORECASE)

# Transcript chunks shorter than this may legitimately repeat ("evet, evet"),
# so only longer ones are treated as duplicates.
_REPEAT_MIN = 12


def _is_repeat_chunk(txt: str, buf: list) -> bool:
    """True if this transcript chunk has already been seen this turn.

    Guards against the API re-sending the tail of a response across the several
    turn_completes a tool-using turn produces.
    """
    if len(txt) < _REPEAT_MIN:
        return bool(buf) and txt == buf[-1]
    joined = " ".join(buf)
    return txt in joined

def _clean_transcript(text: str) -> str:    
    text = _CTRL_RE.sub("", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)
    return text.strip()

TOOL_DECLARATIONS = [
    # ── Inline tools ─────────────────────────────────────────────────────────
    # These stay here (rather than in an actions/*.py TOOL dict) because their
    # handling is woven into live-session state — vision capture/injection,
    # camera stream, memory writes, the monitor engine, and shutdown. All other
    # tools live in their own action file and are auto-discovered by
    # core.action_loader (see JarvisLive.__init__).
    {
        "name": "system_status",
        "description": (
            "Returns real-time system metrics: CPU usage, RAM, GPU load, CPU temperature, "
            "uptime, and process count. Use when the user asks about computer performance, "
            "temperature, memory, or resource usage."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
        "name": "screen_process",
        "description": (
            "Captures the screen or webcam image and lets you analyze it. "
            "MUST be called when user asks what is on screen, what you see, "
            "look at camera, analyze my screen, etc. "
            "You have NO visual ability without this tool. "
            "After the image is captured it is sent directly to you — describe what you see and answer the user's question. "
            "When using camera: the live view stays open until user says close it or calls close_camera."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "angle": {"type": "STRING", "description": "'screen' to capture display, 'camera' for webcam. Default: 'screen'"},
                "text":  {"type": "STRING", "description": "The question or instruction about the captured image"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "close_camera",
        "description": (
            "Closes the live camera view shown on screen. "
            "Call when the user says (in ANY language): close camera, stop camera, "
            "turn off camera, that's creepy, etc."
        ),
        "parameters": {"type": "OBJECT", "properties": {}, "required": []}
    },
    {
        "name": "manage_monitor",
        "description": (
            "Add, remove, or list background monitoring topics. "
            "JARVIS checks these topics once a day and alerts the user when there is a new development. "
            "Use 'add' when the user says 'monitor X', 'track X', 'follow X'. "
            "Use 'remove' when the user says 'stop monitoring X'. "
            "Use 'list' when the user asks what is being monitored. "
            "Do NOT add crypto, financial, or trading topics."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type":        "STRING",
                    "description": "add | remove | list",
                },
                "topic": {
                    "type":        "STRING",
                    "description": "Topic to monitor or stop monitoring (e.g. 'space exploration', 'AI news')",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "shutdown_jarvis",
        "description": (
            "Shuts down the assistant completely. "
            "Call this when the user expresses intent to end the conversation, "
            "close the assistant, say goodbye, or stop Jarvis. "
            "The user can say this in ANY language. "
            "Calling this puts a confirmation on the user's screen — the "
            "assistant does NOT shut down until they press CONFIRM. Say that a "
            "confirmation is waiting and never claim it is already done. "
            "NEVER call it before the user has spoken or typed something in "
            "this session — the startup briefing does not count."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
        "name": "save_memory",
        "description": (
            "Save an important personal fact about the user to long-term memory. "
            "Call this silently whenever the user reveals something worth remembering: "
            "name, age, city, job, preferences, hobbies, relationships, projects, or future plans. "
            "Do NOT call for: weather, reminders, searches, or one-time commands. "
            "Do NOT announce that you are saving — just call it silently. "
            "Values must be in English regardless of the conversation language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": (
                        "identity — name, age, birthday, city, job, language, nationality | "
                        "preferences — favorite food/color/music/film/game/sport, hobbies | "
                        "projects — active projects, goals, things being built | "
                        "relationships — friends, family, partner, colleagues | "
                        "wishes — future plans, things to buy, travel dreams | "
                        "notes — habits, schedule, anything else worth remembering"
                    )
                },
                "key":   {"type": "STRING", "description": "Short snake_case key (e.g. name, favorite_food, sister_name)"},
                "value": {"type": "STRING", "description": "Concise value in English (e.g. Fatih, pizza, older sister)"},
            },
            "required": ["category", "key", "value"]
        }
    },
    {
        "name": "recall_memory",
        "description": (
            "Look up a fact you have stored about the user but which is NOT in "
            "the memory block of your system prompt. "
            "The prompt lists the keys it did not have room for under "
            "'[ALSO REMEMBERED]' — if the user asks about anything named there, "
            "call this FIRST. "
            "Also call it before saying you do not know something personal, and "
            "when the user asks what you remember about them (leave query empty "
            "for everything). "
            "This is a local file search: it is instant and costs nothing."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": (
                        "Keyword to search for — a name, a topic, a category "
                        "(e.g. 'ayse', 'coffee', 'projects'). "
                        "Leave empty to list everything stored."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "undo",
        "description": (
            "Reverse the last change YOU made to this computer — a file you "
            "moved, renamed, created or wrote, or a setting you changed such as "
            "volume, brightness, dark mode or WiFi. "
            "Call this whenever the user says undo, revert, take it back, put it "
            "back, cancel that, or tells you that you did the wrong thing, in ANY "
            "language. "
            "Use action='list' when they ask what can be undone. "
            "This only covers your own actions — it is not the Ctrl+Z of whatever "
            "application is on screen (that is computer_settings with action 'undo')."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "undo (default) — reverse the last change | list — show what can be undone",
                },
            },
            "required": [],
        },
    },
]

# ── V3 dispatch metadata for the inline tools ─────────────────────────────────
# Parallel to TOOL_DECLARATIONS above, deliberately SEPARATE: every declaration
# in that list goes straight to Gemini as a function_declaration, and these
# six keys are not valid there. core.action_loader parses the same six keys
# out of each actions/*.py TOOL dict; this table is their inline twin, and
# main.py's _tool_policy() reads one or the other for every dispatch.
# (Plugins get the safe defaults — no entry, no gate, no permissions.)
INLINE_TOOL_META: dict[str, dict] = {
    "system_status": {
        "risk": "safe", "timeout": 30,
        "permissions": [],
        "requires_confirmation": False, "supports_undo": False,
        "verification": "none",
    },
    "screen_process": {
        "risk": "safe", "timeout": 45,
        "permissions": ["capture"],
        "requires_confirmation": False, "supports_undo": False,
        "verification": "none",
    },
    "close_camera": {
        "risk": "safe", "timeout": 15,
        "permissions": ["capture"],
        "requires_confirmation": False, "supports_undo": False,
        "verification": "none",
    },
    "manage_monitor": {
        "risk": "safe", "timeout": 30,
        "permissions": ["desktop"],
        "requires_confirmation": False, "supports_undo": False,
        "verification": "none",
    },
    # Ends the program: nothing after it can be undone by the assistant, so a
    # human presses CONFIRM — the same gate computer_settings uses for the
    # computer's own shutdown/restart.
    "shutdown_jarvis": {
        "risk": "dangerous", "timeout": 30,
        "permissions": ["process"],
        "requires_confirmation": True, "supports_undo": False,
        "verification": "none",
    },
    "save_memory": {
        "risk": "safe", "timeout": 15,
        "permissions": ["memory"],
        "requires_confirmation": False, "supports_undo": False,
        "verification": "none",
    },
    "recall_memory": {
        "risk": "safe", "timeout": 15,
        "permissions": ["memory"],
        "requires_confirmation": False, "supports_undo": False,
        "verification": "none",
    },
    "undo": {
        "risk": "safe", "timeout": 30,
        "permissions": [],
        "requires_confirmation": False, "supports_undo": False,
        "verification": "none",
    },
}

# Phase 40: the fallback ledger for dispatch surfaces built without __init__
# (`__new__`-constructed test fakes). Everything granted — identical to a
# missing config/permissions.json, which is the pre-Phase-40 behavior. The
# real instance created in JarvisLive.__init__ loads the config file.
_DEFAULT_PERMISSIONS = PermissionLedger(load=False)

# ── M8: post-action verification (§3.13) ─────────────────────────────────────
# core/verifier.py has been dormant since Phase 1. It runs now, for every tool
# that declares verification="verifier" and carries a `verify` callable in its
# TOOL dict — the probe lives next to the action it checks (open_app checks the
# process, browser_control reads the URL back, computer_control checks the
# file/window). What the Verifier appends is what keeps "Opened X." from
# becoming a claim nobody checked.
VERIFIER = Verifier()
VERIFY_TIMEOUT_S = 5.0


def verification_note(vres) -> str:
    """The model-facing verdict, appended to the tool's result.

    Each status gets its own wording because each is a different claim:
    VERIFIED may be reported as done; FAILED must never be; PARTIAL and
    NOT VERIFIED must be said as uncertainty, not dressed up as success.
    """
    msg = (vres.message or "").strip()
    if vres.status == VerificationStatus.VERIFIED:
        return f"\n[VERIFIED] {msg}"
    if vres.status == VerificationStatus.FAILED:
        return (f"\n[VERIFICATION FAILED] {msg}. "
                f"Do NOT claim this action succeeded — say what was actually "
                f"confirmed and what was not.")
    if vres.status == VerificationStatus.PARTIAL:
        return f"\n[PARTIALLY VERIFIED] {msg}"
    return f"\n[NOT VERIFIED] {msg}"


def _register_verifier_rules(registry, verifier=None) -> int:
    """Bind every action's `verify` callable into the Verifier.

    Called once at startup with the action registry; returns how many rules
    were registered (0 is legal — a build with no verifiable tools still has
    to boot).
    """
    verifier = VERIFIER if verifier is None else verifier
    count = 0
    for name in sorted(registry.names()):
        fn = registry.verify_fn(name)
        if fn is None:
            continue
        verifier.register_rule(VerificationRule(
            tool_name=name,
            strategy=VerificationStrategy.CUSTOM,
            check_fn=fn,
        ))
        count += 1
    if count:
        print(f"[Verify] {count} verification rule(s) registered")
    return count


# ── M9: one content surface (§3.2) ───────────────────────────────────────────
# ContentPanel has existed since v1, but only web_search and the news briefing
# ever fed it — every other reference-shaped result (reports, lists, summaries)
# lived and died in the spoken transcript. These declare which actions' results
# belong on screen; _surface_content() is the single route there: actions,
# plugins (§3.10) and the web_search special case all pass through it.
_CONTENT_SURFACE: dict[str, str] = {
    "youtube_video":     "YOUTUBE",
    "flight_finder":     "FLIGHTS",
    "code_helper":       "CODE",
    "dev_agent":         "DEV",
    "file_processor":    "FILE",
    "file_intelligence": "FILE",
}
# Spoken-scale results ("Opened X.", one-line answers) stay in the voice.
_CONTENT_MIN_CHARS = 120
# Failure-shaped results are never put on screen as if they were content.
_CONTENT_SKIP = ("No results", "Search failed", "Unknown", "Tool '",
                 "Could not", "Browser error", "I cannot run",
                 "not available")


class _ReconnectSignal(Exception):
    """Raised inside the session TaskGroup to force a clean, voluntary reconnect
    (e.g. the user picked a new voice — the voice is fixed at connect time, so
    the session must be rebuilt).

    Carries `keep_context`: True for an ordinary rebuild, where the stored
    resumption handle is replayed and the conversation continues; False when the
    new session must genuinely start clean (see the voice-change note in
    _on_voice_change)."""

    def __init__(self, keep_context: bool = True):
        super().__init__()
        self.keep_context = keep_context


def _is_reconnect_signal(exc: BaseException) -> bool:
    """True if `exc` is a _ReconnectSignal, or a(n) (Base)ExceptionGroup that
    wraps one — TaskGroup bundles child exceptions into a group."""
    if isinstance(exc, _ReconnectSignal):
        return True
    if isinstance(exc, BaseExceptionGroup):
        return any(_is_reconnect_signal(sub) for sub in exc.exceptions)
    return False


def _keep_context_of(exc: BaseException) -> bool:
    """Read `keep_context` off a reconnect signal, unwrapping the group the
    TaskGroup put it in. Defaults to True: an unexpected shape must not silently
    wipe the conversation."""
    if isinstance(exc, _ReconnectSignal):
        return getattr(exc, "keep_context", True)
    if isinstance(exc, BaseExceptionGroup):
        for sub in exc.exceptions:
            if _is_reconnect_signal(sub):
                return _keep_context_of(sub)
    return True


def _is_resumption_rejection(exc: BaseException, resumed_with: bool) -> bool:
    """True when the server refused the resumption handle we replayed.

    The handle has a limited life; without this check the same dead handle
    would be replayed on every retry and the assistant would never come back —
    the feature meant to survive a reconnect would be the thing preventing
    one. Classification lives here rather than inline in run() so reconnect
    behaviour is unit-testable (§3.8, M6).
    """
    if not resumed_with:
        return False
    s = str(exc)
    return ("resum" in s.lower()
            or "handle" in s.lower()
            or "INVALID_ARGUMENT" in s
            or "NOT_FOUND" in s)


class JarvisLive:
    def __init__(self, ui: JarvisUI):
        self.ui             = ui
        self._asst_name     = "JARVI    S"   # updated each session from config
        self.session              = None
        self.audio_in_queue       = None
        self.out_queue            = None
        self._loop                     = None
        self._is_speaking         = False
        self._speaking_lock       = threading.Lock()
        self._phone_active        = False   # True while phone mic is streaming; pauses PC mic
        self._pending_vision       = None    # (img_bytes, mime_type, question, angle) to inject after tool response
        self._memory_notes: list[str] = []  # memory writes pending injection into the live context
        # Session-lifetime handoff slot for tools (weather's last search, …).
        # Was always None before — Phase 2 residual made it a real object.
        self.session_memory      = SessionMemory()
        self._vision_cam_active    = False   # True if camera was opened for vision → auto-close after response
        self._vision_close_pending = False   # True after vision injected; next turn_complete closes camera
        self._vision_last_time     = 0.0     # monotonic time of last screen_process call (cooldown guard)
        self._vision_busy          = False   # True while a vision capture/inject cycle is in flight
        self._interrupted          = False   # True while draining audio after user interrupt
        # Transcript-driven mouth shapes for the avatar. Fed from the receive
        # loop as words arrive, drained by the playback loop against the audio.
        self._visemes              = VisemeStream()
        self._last_out_logged      = ""      # de-dupes a re-sent transcript tail
        # Push-to-talk
        self._ptt_enabled          = False
        self._ptt_held             = False
        self._ptt                  = None    # core.hotkey.PushToTalk
        self._out_level            = 0.0     # level of the audio being played right now
        self._echo                 = EchoGuard()
        # `stream.write()` returns when the buffer accepts the audio, not when the
        # speaker has finished with it, so sound is still in the room after the
        # speaking flag drops. Streaming the microphone during that gap is how an
        # assistant ends up answering itself. Measured from the device rather than
        # guessed; see _play_audio.
        self._out_latency          = 0.20    # seconds, replaced with the real value
        self._tail_until           = 0.0     # monotonic time the echo tail expires
        self._barge_blocks         = 0       # consecutive voice-positive blocks for barge-in
        # Wall-clock time at which the audio written next will begin to sound.
        # The mouth is scheduled against this, never against "now": batches are
        # handed to the device far faster than they play, so "now" ran the lips
        # ahead of the words and cut every schedule short. 0 = nothing playing.
        self._play_cursor          = 0.0
        self.ui.on_push_to_talk   = self.set_push_to_talk
        self.ui.ptt_hold          = self._on_ptt
        self.ui.on_text_command   = self._on_text_command
        self.ui.on_remote_clicked = self._make_remote_key
        self.ui.on_interrupt      = self.interrupt
        self.ui.on_voice_change   = self._on_voice_change     # voice picker → rebuild session
        self.ui.on_audio_device_change = self._on_audio_device_change
        self._reconnect_event: asyncio.Event | None = None
        self._reconnect_keep = True   # False → next rebuild drops the resumption handle

        # ── Session resumption ─────────────────────────────────────────
        # The server issues a resumption handle every few seconds and reissues
        # it as the conversation moves on. Before this, session_resumption was
        # switched ON in the config and the update was never read, so the handle
        # was thrown away and EVERY reconnect — a dropped packet, a voice change,
        # switching microphone — started an empty session. "Unlimited sessions"
        # leaked through exactly this hole.
        #
        # Deliberately in RAM only, never written to disk. Persisting it would
        # make a fresh launch continue yesterday's conversation, which sounds
        # appealing but breaks the session-summary flow: _save_session_summary
        # runs at shutdown and the morning briefing pops it the next day. A
        # conversation that never ends never produces a summary, and the
        # "yesterday we talked about…" line silently disappears.
        self._resume_handle: str | None = None
        self._turn_done_event: asyncio.Event | None = None
        self._dashboard     = None
        self._event_bus     = EventBus()      # M11: started in run(), not here
        self._permissions   = PermissionLedger()  # Phase 40: config/permissions.json
        self._activity      = ActivityFeed()  # Phase 32: HUD activity ring (§3.15)
        self._briefing_sent    = False          # morning briefing fires once per process
        self._sys_monitor      = SystemMonitor()  # persistent cooldown state
        self._proactive        = ProactiveEngine()
        self._last_user_speech = time.monotonic()  # updated on every user utterance
        self._heard_user_text   = False  # True once real user input reaches the
                                         # session (mic transcript / typed) — the
                                         # shutdown gate refuses without it
        self._session_log: list[str] = []          # conversation turns for end-of-session summary

        # ── M14: degraded offline voice (§3.6) ───────────────────────────
        # True ⇔ the degraded voice loop is running: the Live API has failed
        # three times in a row and voice has fallen back to local STT + the
        # REST ladder + offline TTS. Cleared the moment a session connects.
        self._degraded          = False
        self._degraded_failures = 0
        self._offline           = None   # lazy OfflineVoice, never at startup

        self._enhanced_live = True  # proactive audio; auto-disabled if the server rejects it
        self._tuned_live    = True  # turn-taking / media / thinking knobs; same fallback

        _base_dir = Path(__file__).resolve().parent
        _inline_names = {t["name"] for t in TOOL_DECLARATIONS}

        # File-backed tools: every actions/*.py with a TOOL dict, discovered the
        # same way plugins are. Reserved names = the inline tools above, so an
        # action can never shadow one.
        self._action_registry = discover_actions(
            actions_dir=_base_dir / "actions",
            reserved_names=_inline_names,
            logger=lambda msg: print(f"[Actions] {msg}"),
        )
        # M8: bind the probes the actions declared (verification="verifier")
        # into the shared Verifier, before any dispatch can happen.
        _register_verifier_rules(self._action_registry)
        _perf_mark("discover_actions")

        # Plugins must not collide with either an inline tool or a discovered action.
        _core_names = _inline_names | self._action_registry.names()
        self._plugin_registry = discover_plugins(
            plugins_dir=_base_dir / "plugins",
            core_tool_names=_core_names,
            # Console gets the full boot transcript; the activity log gets only
            # what the user has to know about. Every plugin loading correctly is
            # the expected case and does not belong in their conversation.
            logger=lambda msg: print(f"[Plugins] {msg}"),
            notify=lambda msg: self.ui.write_log(f"SYS: {msg}"),
        )
        _perf_mark("discover_plugins")
        self.ui.get_plugins = self._plugin_registry.list_for_ui
        self.ui.get_plugin_settings = self._plugin_registry.settings_schemas  # ⚙ settings tab
        self.ui.request_say = self.plugin_say   # plugins: mid-task speech channel

        # ── Automation Intelligence V0.1 (Phase 7) ─────────────────────────
        # One engine, injected with the EXISTING systems (dispatcher gate,
        # confirm banner, gemini, memory, notifications, event bus). Any
        # failure here degrades automation to "unavailable" — it must never
        # take the app down.
        self._automation = None
        try:
            from core.automation.engine import AutomationEngine, set_engine
            from core.automation.adapters import (
                build_ai_call, build_confirm, build_memory_recall,
                build_memory_save, build_notify, build_tool_call,
            )
            from memory.memory_manager import remember as _mem_remember, \
                search_memory as _mem_search
            _aconfirm = build_confirm(request=confirm_gate.request,
                                      pending=confirm_gate.pending_title)
            self._automation = AutomationEngine(
                ledger=self._permissions,
                tool_call=build_tool_call(
                    dispatch=self._dispatch_tool,
                    policy_of=self._tool_policy,
                    confirm=_aconfirm,
                    ledger=self._permissions,
                    pending=confirm_gate.pending_title,
                    heard_user_text=lambda: self._heard_user_text),
                confirm=_aconfirm,
                ai_call=build_ai_call(),
                memory_save=build_memory_save(_mem_remember),
                memory_recall=build_memory_recall(_mem_search),
                notify=self._automation_notify,
                emit=self._automation_emit,
                log=lambda m: print(m),
            )
            set_engine(self._automation)
            self._automation.refresh_catalog(self._action_registry,
                                             self._plugin_registry,
                                             INLINE_TOOL_META)
            print("[Automation] engine ready")
        except Exception as e:
            print(f"[Automation] engine disabled: {e}")
            self._automation = None

        # ── Persona Engine ────────────────────────────────────────────────
        from core.persona_registry import PersonaRegistry
        from memory.config_manager import get_active_persona as _get_persona_cfg
        self._persona_registry = PersonaRegistry()
        self._persona_registry.load_from_config(_get_persona_cfg())
        self.ui.get_persona_registry = lambda: self._persona_registry
        self.ui.on_persona_change = self._on_persona_change

        # Set initial persona indicator in the UI
        _active_persona = self._persona_registry.get_active()
        self.ui.update_persona_indicator(_active_persona.id)

        # ── Wake word ────────────────────────────────────────────────────────
        # _awake gates the mic (see _listen_audio) and the background speakers.
        # It is True whenever wake word is OFF, so default behaviour is unchanged.
        self._wake_enabled     = get_wake_word_enabled()
        self._awake            = not self._wake_enabled
        self._wake_detector: WakeWordDetector | None = None
        self._wake_sleep_timeout = WAKE_SLEEP_TIMEOUT

        # Restore the saved push-to-talk preference. Doing it here rather than
        # in __init__ means the hotkey thread only exists once there is a
        # session to talk to.
        if get_push_to_talk_enabled():
            try:
                self.set_push_to_talk(True)
            except Exception as e:
                print(f"[JARVIS] ⚠ Push-to-talk unavailable: {e}")
        # UI control surface for the Wake Word settings section.
        self.ui.wake_is_ready    = wake_is_ready          # () -> bool
        self.ui.wake_get_state   = self._wake_state       # () -> dict
        self.ui.on_wake_toggle   = self._ui_wake_toggle   # (enable: bool) -> str
        self.ui.on_wake_manual   = self._ui_wake_manual   # () -> toggle awake/asleep
        self.ui.on_wake_install  = self._ui_wake_install  # () -> (ok, msg)

        # ── Gesture Control (Spatial Interaction Engine) ─────────────────────
        # The engine's import chain ends in mediapipe (~2-3 s, the biggest
        # optional cost in the §3.19 startup benchmark), so it is built on
        # demand: now only when the setting is already on, otherwise on the
        # first UI request through _ensure_gesture_engine.
        self._gesture_engine = None
        self.ui.on_gesture_engine_needed = self._ensure_gesture_engine
        try:
            from memory.config_manager import get_gesture_settings as _gs
            if _gs().get("gesture_enabled", False):
                self._ensure_gesture_engine()
                self._gesture_engine.start()
                print("[Spatial] Gesture control enabled")
        except ImportError as e:
            print(f"[Spatial] Gesture engine unavailable: {e}")
        except Exception as e:
            print(f"[Spatial] Gesture engine init error: {e}")
        _perf_mark("spatial_init")

    def _ensure_gesture_engine(self):
        """Build the spatial engine on first need (lazy: mediapipe import).

        Idempotent — every call returns the same engine. Never starts it;
        boot auto-start and the settings toggle own start().
        """
        if self._gesture_engine is None:
            from core.spatial.engine import SpatialInteractionEngine
            from core.spatial.interaction_state import GestureSettings
            from memory.config_manager import get_gesture_settings as _gs

            _gcfg = _gs()
            _gs_obj = GestureSettings(
                enabled=_gcfg.get("gesture_enabled", False),
                camera_index=_gcfg.get("gesture_camera_index", 0),
                confidence_threshold=_gcfg.get("gesture_confidence_threshold", 0.7),
                pointer_smoothing=_gcfg.get("gesture_smoothing", 0.3),
                mirror_mode=_gcfg.get("gesture_mirror_mode", True),
            )
            self._gesture_engine = SpatialInteractionEngine(_gs_obj)
            self._gesture_engine.on_gesture_event = self._on_gesture_event
            self.ui.set_gesture_engine(self._gesture_engine)
        return self._gesture_engine

    # ── Wake word: state machine ─────────────────────────────────────────────

    def _wake_state(self) -> dict:
        # A loaded, running detector is definitively ready; otherwise fall back
        # to the cheap on-disk model-file check (no Model construction).
        ready = bool(self._wake_detector and self._wake_detector.ready) or wake_is_ready()
        return {"enabled": self._wake_enabled, "awake": self._awake, "ready": ready}

    def _ensure_wake_detector(self) -> bool:
        """Load the detector once (model loads on first start). Idempotent."""
        if self._wake_detector is None:
            self._wake_detector = WakeWordDetector(
                on_detect=self._on_wake_detected,
                logger=lambda m: print(f"[Wake] {m}"),
                notify=lambda m: self.ui.write_log(f"SYS: {m}"),
            )
        if not self._wake_detector.ready:
            return self._wake_detector.start()
        return True

    def _on_gesture_event(self, event) -> None:
        """Handle gesture events from the spatial engine."""
        try:
            if hasattr(event, 'pointer_position') and event.pointer_position != (0.0, 0.0):
                self.ui.emit_gesture_event("pointer_move", event.pointer_position)
            if hasattr(event, 'intent'):
                self.ui.emit_gesture_event("intent", str(event.intent.value))
            self.ui.emit_gesture_state(str(self._gesture_engine.state.name) if self._gesture_engine else "INACTIVE")
        except Exception as e:
            print(f"[Spatial] Gesture event error: {e}")

    def _on_wake_detected(self) -> None:
        """Called from the detector thread when 'Hey Jarvis' is heard."""
        self.wake(reason="wake word")

    def wake(self, reason: str = "wake word") -> None:
        if self._awake:
            return
        self._awake = True
        self._last_user_speech = time.monotonic()   # start the auto-sleep clock now
        if not self.ui.muted:
            self.ui.set_state("LISTENING")
        self.ui.write_log(f"SYS: Awake — {reason}.")

    def sleep(self, reason: str = "timeout") -> None:
        if not self._awake:
            return
        self._awake = False
        self.set_speaking(False)
        self.ui.set_state("SLEEPING")
        self.ui.write_log(f"SYS: Sleeping — {reason}. Say 'Hey Jarvis' to wake me.")

    async def _run_sleep_watch(self) -> None:
        """Auto-sleep after the configured silence window (wake-word mode only)."""
        while True:
            await asyncio.sleep(5)
            if not self._wake_enabled or not self._awake:
                continue
            with self._speaking_lock:
                speaking = self._is_speaking
            if speaking:
                continue
            if (time.monotonic() - self._last_user_speech) > self._wake_sleep_timeout:
                self.sleep(reason="no speech for 2 minutes")

    # ── Wake word: UI callbacks (called from the Qt thread) ──────────────────

    def _ui_wake_toggle(self, enable: bool) -> str:
        """Enable/disable wake word from the settings UI. Returns a status token:
        'enabled' | 'disabled' | 'need_download'."""
        if enable:
            if not wake_is_ready():
                return "need_download"
            self._wake_enabled = True
            save_wake_word_enabled(True)
            self._ensure_wake_detector()
            self.sleep(reason="wake word enabled")
            return "enabled"
        else:
            self._wake_enabled = False
            save_wake_word_enabled(False)
            self.wake(reason="wake word disabled")
            return "disabled"

    def _ui_wake_manual(self) -> None:
        """Manual sleep/wake button in the UI."""
        if not self._wake_enabled:
            return
        if self._awake:
            self.sleep(reason="you tapped sleep")
        else:
            self.wake(reason="you tapped wake")

    def _ui_wake_install(self) -> tuple[bool, str]:
        """Download openwakeword + the model (runs in a UI worker thread)."""
        # Triggered by the user pressing the button, so its progress is exactly
        # what they are waiting to see.
        return wake_install(logger=lambda m: print(f"[Wake] {m}"),
                            notify=lambda m: self.ui.write_log(f"SYS: {m}"))

    def plugin_say(self, instruction: str) -> None:
        """
        Thread-safe speech channel for plugins: lets a plugin ask JARVIS to
        say something short WHILE its run() is still executing (plugins block
        their executor thread, so they can't speak through the tool response
        until they finish). The instruction is injected into the Live session
        exactly like a proactive check-in; Gemini phrases it naturally in the
        user's language. Silently a no-op when no session is connected.
        """
        loop = getattr(self, "_loop", None)
        if not loop or not self.session:
            return

        async def _say():
            try:
                await self.session.send_client_content(
                    turns={"role": "user", "parts": [{"text": instruction}]},
                    turn_complete=True,
                )
            except Exception as e:
                print(f"[PluginSay] {e}")

        try:
            asyncio.run_coroutine_threadsafe(_say(), loop)
        except Exception as e:
            print(f"[PluginSay] {e}")

    def request_reconnect(self, keep_context: bool = True, reason: str = ""):
        """Thread-safe: ask the run loop to tear down and rebuild the Live
        session. Called from the Qt thread. No-op until the async loop and
        reconnect event exist.

        `keep_context=False` drops the resumption handle so the new session
        starts empty — only for changes the server cannot apply to a resumed
        session."""
        loop = getattr(self, "_loop", None)
        ev   = self._reconnect_event
        self._reconnect_keep   = keep_context
        self._reconnect_reason = reason
        if loop and ev is not None:
            loop.call_soon_threadsafe(ev.set)

    def _on_voice_change(self):
        """Voice picker applied.

        The voice is baked into the session at connect time, so a rebuild is
        required. It is rebuilt WITHOUT the resumption handle on purpose:
        resuming restores the server's own session state, and the safe reading
        is that it restores the voice with it — which would make the picker
        appear to do nothing. Losing context here is acceptable because changing
        voice is a deliberate, rare act; losing it on a dropped packet was not."""
        self.request_reconnect(keep_context=False, reason="new voice")

    def _on_persona_change(self, persona_id: str):
        """Persona selector applied.

        Switches the active persona, updates the voice mapping, and
        rebuilds the session so the new persona prompt takes effect.
        The conversation memory is preserved — only the presentation
        layer changes."""
        from memory.config_manager import save_active_persona, save_voice

        old_name = self._persona_registry.get_active_name()
        try:
            new_persona = self._persona_registry.switch_to(persona_id)
        except ValueError as exc:
            print(f"[Persona] ⚠ {exc}")
            return

        # Persist the choice and update the voice
        save_active_persona(persona_id)
        save_voice(new_persona.voice_id)

        print(
            f"[Persona] {old_name} → {new_persona.name} "
            f"({new_persona.emoji}, voice={new_persona.voice_id})"
        )

        # Reconnect with the new persona + voice (lose context on voice change)
        self.request_reconnect(keep_context=False, reason=f"persona switch to {new_persona.name}")

        # Update the UI persona indicator
        self.ui.update_persona_indicator(persona_id)

    def _detect_persona_switch(self, text: str) -> str | None:
        """Detect persona switch intents in user text.

        Returns the persona id if detected, None otherwise.
        Matches patterns like:
            "switch to fenrir", "use kore", "charon mode",
            "I want aoede", "activate leda", "be fenrir",
            "fenrir", "kore", etc.
        """
        from core.persona import PERSONA_BY_ID

        _text = text.strip().lower()

        # Direct name mention (single word)
        if _text in PERSONA_BY_ID:
            return _text

        # Pattern matching
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

    def _on_audio_device_change(self):
        """Microphone or speaker changed. Both streams are opened inside the
        session TaskGroup, so they can only be re-opened by rebuilding it —
        but the conversation is kept, which is the whole reason resumption
        landed before this feature did."""
        self.request_reconnect(keep_context=True, reason="audio device")

    async def _watch_reconnect(self):
        """Session-scoped task: when a voluntary reconnect is requested, raise a
        signal that unwinds the TaskGroup so the run loop rebuilds the session."""
        assert self._reconnect_event is not None
        await self._reconnect_event.wait()
        self._reconnect_event.clear()
        keep   = self._reconnect_keep
        reason = getattr(self, "_reconnect_reason", "") or "settings"
        self.ui.write_log(
            f"SYS: Applying {reason} — reconnecting"
            + ("..." if keep else " (starting a fresh conversation)...")
        )
        raise _ReconnectSignal(keep_context=keep)

    def _prepare_voluntary_reconnect(self, exc: BaseException) -> None:
        """Run-loop bookkeeping for a voluntary reconnect (§3.8, M6).

        keep_context=False is a deliberate clean slate (voice/persona change):
        drop the resumption handle so the next connect really does start empty.
        keep_context=True replays the handle and the conversation continues.
        Backoff resets either way — a requested reconnect is not a failure and
        must not wait.
        """
        if not _keep_context_of(exc):
            self._resume_handle = None
        self._conn_backoff = 0

    def _note_resumption_update(self, sru) -> None:
        """Track a server session_resumption update (§3.8, M6).

        `resumable` goes false while a turn is mid-flight — replaying a handle
        from that moment is exactly what the flag exists to prevent — so only
        resumable handles are kept, and each new handle replaces the last.
        """
        if sru is None:
            return
        if getattr(sru, "resumable", False) and getattr(sru, "new_handle", None):
            if self._resume_handle is None:
                print("[JARVIS] 🔗 Session resumption armed")
            self._resume_handle = sru.new_handle

    def _make_remote_key(self):
        """Called from Qt main thread when user presses Remote Control."""
        if self._dashboard is None:
            self.ui.write_log(
                "SYS: Dashboard unavailable. "
                "Run: pip install fastapi \"uvicorn[standard]\" cryptography"
            )
            return None
        key    = self._dashboard.new_key()
        url    = self._dashboard.get_url()
        manual = self._dashboard.get_manual_url()
        return url, key, f"{url}/auto-login?key={key}", manual

    def _on_text_command(self, text: str):
        # Typed input is the user, by definition — record it for the shutdown
        # gate even if this particular command is dropped below.
        self._heard_user_text = True
        print(f"[Input] typed command: {text!r}", flush=True)
        if not self._loop or not self.session:
            return
        # Respect wake-word sleep: a typed command must not be answered while
        # asleep either (the sleep gate is not just for the mic). Wake first with
        # "Hey Jarvis" or the WAKE NOW button.
        if self._wake_enabled and not self._awake:
            self.ui.write_log("SYS: I'm asleep — say 'Hey Jarvis' or tap WAKE NOW first.")
            return

        # ── Persona switch intercept ────────────────────────────────────
        # Detect "switch to <persona>", "use <persona>", "<persona> mode",
        # etc. before sending to the LLM so the switch is instant.
        _persona_id = self._detect_persona_switch(text)
        if _persona_id:
            self._on_persona_change(_persona_id)
            return

        # Typed input skips transcription but not understanding.
        if not self.ui.muted:
            self.ui.set_state("UNDERSTANDING")

        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"role": "user", "parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def _tail_active(self) -> bool:
        """True while the speakers may still be finishing our last sentence."""
        return time.monotonic() < self._tail_until

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            self._is_speaking = value
        if value:
            self._tail_until = 0.0
            self._barge_blocks = 0   # reset barge-in counter at start of each turn
        else:
            # Hold the guard open across the device's own output latency plus a
            # margin for the room. The microphone is NOT muted during it — the
            # guard still lets a genuine reply through, so answering instantly
            # still works. Only our own echo is dropped.
            self._tail_until = time.monotonic() + self._out_latency + _TAIL_MARGIN
        if not value:
            # The echo history is deliberately NOT cleared here: the tail above
            # still needs it to recognise our own voice. It is dropped when the
            # tail expires. What the guard learned about the room always stays.
            self._out_level = 0.0
        if value:
            self.ui.set_state("SPEAKING")
        elif not self.ui.muted:
            self.ui.set_state("LISTENING")

    def set_push_to_talk(self, enabled: bool) -> str:
        """Turn hold-to-talk on or off. Returns the scope actually achieved."""
        from core.hotkey import PushToTalk

        self._ptt_enabled = bool(enabled)
        self._ptt_held = False
        if not enabled:
            if self._ptt is not None:
                self._ptt.stop()
                self._ptt = None
            return "off"

        if self._ptt is None:
            self._ptt = PushToTalk(self._on_ptt)
        scope = self._ptt.start()
        # A window-scoped chord is a real limitation, not a detail — say it once
        # in the log so nobody wonders why it does nothing while another app is
        # focused. Reporting it must never be able to undo the thing it reports.
        try:
            self.ui.write_log(
                f"SYS: Push-to-talk on — hold {self._ptt.label}"
                + ("." if scope == "global"
                   else " (works while this window is focused)."))
        except Exception:
            pass
        return scope

    def _on_ptt(self, held: bool) -> None:
        """Chord pressed or released — may arrive on the hotkey thread."""
        self._ptt_held = held
        if held:
            # Holding the key is also a way to wake it, so push-to-talk works
            # without having to say the wake word first.
            if self._wake_enabled and not self._awake:
                self._awake = True
                self._last_user_speech = time.monotonic()
        try:
            self.ui.set_state("LISTENING" if held else "SLEEPING")
        except Exception:
            pass

    def interrupt(self) -> None:
        """Stop JARVIS mid-speech: drain queued audio and open mic immediately."""
        self._interrupted = True
        q = self.audio_in_queue
        if q:
            drained = 0
            while True:
                try:
                    q.get_nowait()
                    drained += 1
                except Exception:
                    break
            if drained:
                print(f"[JARVIS] ✋ Interrupted — {drained} audio chunks discarded")
        self.set_speaking(False)
        # The words we were about to mouth are never going to be spoken now.
        self._visemes.reset()
        self._play_cursor = 0.0     # next batch starts a fresh timeline
        if self._turn_done_event:
            self._turn_done_event.clear()
        self.ui.write_log("SYS: Interrupted — listening...")

    def speak(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"role": "user", "parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        if not self.ui.muted:
            self.ui.set_state("ERROR")
        self.speak(f"Sir, {tool_name} encountered an error. {short}")

    def _build_config(self) -> types.LiveConnectConfig:
        from datetime import datetime

        # Load customization from config
        try:
            _cfg = json.loads(open(API_CONFIG_PATH, encoding="utf-8").read())
            self._asst_name = (_cfg.get("assistant_name") or "JARVIS").strip()
            _user_name = (_cfg.get("user_name") or "").strip()
        except Exception:
            self._asst_name = "JARVIS"
            _user_name = ""

        memory     = load_memory()
        mem_str    = format_memory_for_prompt(memory)
        sys_prompt = _load_system_prompt()

        now      = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        time_ctx = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {time_str}\n"
            f"Use this to calculate exact times for reminders.\n\n"
        )

        # Identity injection — overrides any hardcoded name in prompt.txt
        # Address form is a property of the language being spoken, so it is
        # stated as a principle rather than a two-language lookup — the model
        # already knows the respectful register of whatever language it is in.
        _addr = (f"ADDRESS: Always call the user '{_user_name}'."
                 if _user_name
                 else 'ADDRESS: Address the user with the ordinary respectful form '
                      'for a superior in the language you are currently speaking — '
                      '"sir" in English, its everyday equivalent in any other '
                      'language. Never an archaic or aristocratic form, and never '
                      'the form from a different language than the one you are '
                      'speaking in this sentence.')
        identity_ctx = (
            f"[IDENTITY]\n"
            f"Your name is {self._asst_name}. "
            f"Always refer to yourself as {self._asst_name}.\n"
            f"{_addr}\n\n"
        )

        # Everything the model is told about *itself* is derived here, not
        # written into prompt.txt: the name comes from config, the platform from
        # the host, the capability list from the registries that were just
        # discovered. Rename the assistant, add a plugin or move to another OS
        # and this follows without anyone editing a prompt.
        _all_decls = (TOOL_DECLARATIONS
                      + self._action_registry.get_tool_declarations()
                      + self._plugin_registry.get_tool_declarations())
        _names = {(d.get("name") if isinstance(d, dict) else getattr(d, "name", ""))
                  for d in _all_decls}
        sys_prompt = _render_prompt(sys_prompt, {
            "assistant_name": self._asst_name,
            "platform": f"{_platform.system()} {_platform.release()}".strip(),
            "capabilities": _describe_tools(_all_decls),
            "limits": _describe_limits(
                has_vision="screen_process" in _names,
                has_mic=True,
            ),
        })

        parts = [time_ctx, identity_ctx]
        if mem_str:
            parts.append(mem_str)

        # Persona Engine — inject active persona profile between memory
        # and the base prompt so the model knows *how* to communicate.
        _persona_prompt = self._persona_registry.get_system_prompt_addition()
        if _persona_prompt:
            parts.append(_persona_prompt)

        parts.append(sys_prompt)

        cfg = dict(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction="\n".join(parts),
            tools=[{"function_declarations": _all_decls}],
            # Hand back the handle captured from the last session_resumption
            # update. `handle=None` is exactly the old behaviour (ask for
            # handles, start fresh), so the first connect of a run is unchanged.
            session_resumption=types.SessionResumptionConfig(
                handle=self._resume_handle
            ),
            # Sliding-window compression: session never dies from a full context
            # window — JARVIS can stay in one conversation for hours
            context_window_compression=types.ContextWindowCompressionConfig(
                sliding_window=types.SlidingWindow(),
            ),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=get_voice()
                    )
                )
            ),
        )
        if self._enhanced_live:
            # Proactive audio: JARVIS stays silent when speech isn't addressed
            # to it (background chatter, talking to someone else in the room).
            # (Affective dialog was dropped: gemini-3.1-flash-live does not
            #  support it, and it never reliably detected tone in practice.
            #  To restore it on a 2.5 native-audio model, add back:
            #  cfg["enable_affective_dialog"] = True )
            if get_proactive_audio_enabled():
                cfg["proactivity"] = types.ProactivityConfig(proactive_audio=True)

        if self._tuned_live:
            cfg.update(self._tuning_config())

        return types.LiveConnectConfig(**cfg)

    def _tuning_config(self) -> dict:
        """The optional knobs, kept apart so one bad field can be dropped wholesale.

        Every one of these is a preview-API field. If a future model release
        stops accepting any of them the connection fails at setup, so the run
        loop turns `_tuned_live` off and reconnects on the plain config rather
        than leaving the user with an assistant that will not start.
        """
        out: dict = {}

        # How long the server waits through a pause before deciding your turn is
        # over. This — not the size of the prompt — is what most of the delay
        # before a reply actually is, and the default has to suit everybody, so
        # it is necessarily cautious.
        turn = get_turn_tuning()
        if turn.get("enabled", True):
            detect = types.AutomaticActivityDetection(
                silence_duration_ms=turn["silence_ms"],
                prefix_padding_ms=turn["prefix_ms"],
            )
            if turn["end_sensitivity"] == "high":
                detect.end_of_speech_sensitivity = types.EndSensitivity.END_SENSITIVITY_HIGH
            elif turn["end_sensitivity"] == "low":
                detect.end_of_speech_sensitivity = types.EndSensitivity.END_SENSITIVITY_LOW
            if turn["start_sensitivity"] == "high":
                detect.start_of_speech_sensitivity = types.StartSensitivity.START_SENSITIVITY_HIGH
            elif turn["start_sensitivity"] == "low":
                detect.start_of_speech_sensitivity = types.StartSensitivity.START_SENSITIVITY_LOW
            out["realtime_input_config"] = types.RealtimeInputConfig(
                automatic_activity_detection=detect)

        # Screenshots and camera frames are tokenised at this resolution and then
        # stay in the session's context. 'medium' keeps on-screen text legible
        # for a fraction of a full-resolution frame.
        res = get_media_resolution()
        if res != "default":
            out["media_resolution"] = {
                "low":    types.MediaResolution.MEDIA_RESOLUTION_LOW,
                "medium": types.MediaResolution.MEDIA_RESOLUTION_MEDIUM,
                "high":   types.MediaResolution.MEDIA_RESOLUTION_HIGH,
            }[res]

        # Thinking is left at the server default deliberately. Forcing the budget
        # to zero was measured on gemini-3.1-flash-live over interleaved trials
        # and did not make the first word arrive sooner — this model does not
        # appear to deliberate on the Live path, so pinning the field only adds a
        # way for a future release to behave differently. Set "thinking_enabled"
        # in config/api_keys.json to true to let it reason instead.
        if get_thinking_enabled():
            out["thinking_config"] = types.ThinkingConfig(thinking_budget=-1)

        return out

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        """Entry point for every model tool call: resolve the V3 dispatch
        policy first (Phase 17), then apply the shared gate and dispatch.

        The gate lives in core/gates.py — the exact same rules the
        automation adapters run, so the two paths cannot drift. denied →
        refuse without running anything; confirm → park behind core/confirm
        so a HUMAN presses CONFIRM and the real dispatch runs from the
        gate's worker thread; otherwise → _dispatch_tool, which enforces
        the policy's timeout.
        """
        name = fc.name
        policy = self._tool_policy(name)
        ledger = getattr(self, "_permissions", None) or _DEFAULT_PERMISSIONS
        gate = evaluate_gates(
            name, policy, ledger=ledger,
            heard_user_text=getattr(self, "_heard_user_text", False),
            busy=bool(confirm_gate.pending_title()))

        if gate.decision == "deny":
            if gate.rule == "forbidden":
                print(f"[Policy] ⛔ {name} — refused (risk=forbidden)")
            elif gate.rule == "permission":
                print(f"[Policy] ⛔ {name} — refused (permission not granted: "
                      f"{', '.join(gate.denied)})")
            elif gate.rule == "user_turn":
                print("[Policy] ✖ shutdown_jarvis refused — "
                      "no user input in this session yet")
            elif gate.rule == "busy":
                print(f"[Policy] 🔒 {name} — refused (a confirmation is "
                      f"already waiting on screen)")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": message_for(gate.rule, name, gate.denied)})

        if gate.decision == "confirm":
            loop = asyncio.get_event_loop()
            detail = self._confirm_detail(name)

            def _sync_run():
                # core/confirm runs this on its worker thread after a
                # human presses CONFIRM. Post the real dispatch back to
                # the session loop — the tool belongs there — and wait
                # for its result so the gate can log it.
                fut = asyncio.run_coroutine_threadsafe(
                    self._dispatch_tool(fc, policy), loop)
                resp = fut.result(timeout=policy.timeout + 15.0)
                r = getattr(resp, "response", None)
                return str((r or {}).get("result") or "Done.")

            sentence = confirm_gate.request(
                key=name,
                title=name.replace("_", " ").capitalize(),
                detail=detail, run=_sync_run)
            print(f"[Policy] 🔒 {name} — parked behind the confirmation gate")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name, response={"result": sentence})

        return await self._dispatch_tool(fc, policy)

    def _tool_policy(self, name: str) -> ToolPolicy:
        """Resolve a tool's dispatch policy: the action registry first (its
        metadata was parsed from the TOOL dict at load), then the inline
        table for live-session tools, then the safe defaults (plugins and
        unknown names). Invalid metadata fails CLOSED — an unreadable risk
        means the gated path, never the open one."""
        pol = self._action_registry.policy(name) if self._action_registry else None
        if pol is not None:
            return pol
        meta = INLINE_TOOL_META.get(name)
        if meta is None:
            return ToolPolicy()
        pol, err = policy_from_meta(meta)
        if pol is None:
            print(f"[Policy] INVALID metadata for '{name}': {err} — failing closed")
            return ToolPolicy(risk="dangerous", requires_confirmation=True,
                              timeout=30)
        return pol

    def _confirm_detail(self, name: str) -> str:
        """First sentence of the tool's declaration — what the banner shows
        the human under the title."""
        desc = ""
        for d in TOOL_DECLARATIONS:
            if isinstance(d, dict) and d.get("name") == name:
                desc = str(d.get("description") or "")
                break
        if not desc:
            desc = self._action_registry.describe(name) if self._action_registry else ""
        first = desc.split(". ")[0].strip() if desc else ""
        return first[:160] or name

    async def _dispatch_tool(self, fc, policy: ToolPolicy | None = None) -> types.FunctionResponse:
        """Run the tool itself — called directly when no gate applies, and
        from core/confirm's worker thread (via run_coroutine_threadsafe)
        when one does. The policy was already applied by _execute_tool, so
        nothing here re-gates: a confirmed run must not ask twice. The
        policy's timeout guards the action/plugin dispatch paths below;
        inline branches carry their own waits (vision cooldowns, session
        hand-offs) and are not double-bounded.
        """
        name = fc.name
        args = dict(fc.args or {})
        if policy is None:
            policy = self._tool_policy(name)

        print(f"[JARVIS] 🔧 {name}  {args}")
        # The model has already decided — what follows is execution, not
        # thinking. VERIFYING is emitted later, only by tools that really
        # check their own output.
        if not self.ui.muted:
            self.ui.set_state("EXECUTING")


        if name == "save_memory":
            category = args.get("category", "notes")
            key      = args.get("key", "")
            value    = args.get("value", "")
            if key and value:
                update_memory({category: {key: {"value": value}}})
                print(f"[Memory] 💾 save_memory: {category}/{key} = {value}")
                self._memory_notes.append(f"{category}/{key} = {value}")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "ok", "silent": True}
            )

        loop   = asyncio.get_event_loop()
        result = "Done."
        _failed = False
        verdict = None      # structured twin of the prose verification note

        try:
            if name == "recall_memory":
                # Local file search: no network, no second model. Kept out of
                # the executor deliberately — it is a dictionary scan over a few
                # hundred short strings, and a thread hop would cost more than
                # the work itself.
                result = search_memory(args.get("query", ""), limit=8)

            elif name == "undo":
                if str(args.get("action", "")).lower().strip() == "list":
                    items = undo_stack.history()
                    result = ("Things I can undo, most recent first:\n"
                              + "\n".join(f"{i+1}. {t}" for i, t in enumerate(items))
                              ) if items else "I have not changed anything I can undo yet."
                else:
                    result = await loop.run_in_executor(None, undo_stack.undo_last)

            elif name == "screen_process":
                import time as _t_mod
                _now = _t_mod.monotonic()
                _cooldown = 4.0  # seconds — covers echo window after speaking ends
                if self._vision_busy or (_now - self._vision_last_time) < _cooldown:
                    _wait = max(0, _cooldown - (_now - self._vision_last_time))
                    print(f"[Vision] ⏳ Cooldown active ({_wait:.1f}s remaining) — ignoring duplicate call")
                    result = "Vision is still processing the previous request. I will not call this again."
                else:
                    self._vision_busy      = True
                    self._vision_last_time = _now
                    angle     = args.get("angle", "screen").lower()
                    user_text = args.get("text", "What do you see?")
                    if angle == "camera":
                        img_b, mime_t = await loop.run_in_executor(None, _capture_camera)
                        self.ui.start_camera_stream()
                        self._vision_cam_active = True
                        print(f"[Vision] 📷 Camera: {len(img_b):,} bytes")
                        _stall = "camera"
                    else:
                        img_b, mime_t = await loop.run_in_executor(None, _capture_screen)
                        print(f"[Vision] 🖥️  Screen: {len(img_b):,} bytes")
                        _stall = "screen"
                    self._pending_vision = (img_b, mime_t, user_text, angle)
                    # The frame is real and in hand — OBSERVING is the honest
                    # word for "a captured view is waiting to be analysed".
                    if not self.ui.muted:
                        self.ui.set_state("OBSERVING")
                    # The image is attached to this same exchange, so there is
                    # nothing to stall for and nothing to announce. Asking for an
                    # acknowledgement here is what produced two spoken answers —
                    # the model filled that turn by answering the question from
                    # imagination, then answered it again once it could see.
                    result = (
                        f"[VISION_ACTIVE] {_stall.capitalize()} captured and attached to this "
                        f"same exchange. Do not acknowledge and do not answer yet — the image "
                        f"is arriving with this result. Reply once, from what you actually see "
                        f"in it."
                    )

            elif name == "close_camera":
                self.ui.stop_camera_stream()
                result = "Camera closed."

            elif name == "system_status":
                r = await loop.run_in_executor(None, get_system_status)
                result = str(r)

            elif name == "manage_monitor":
                action = args.get("action", "").lower().strip()
                topic  = args.get("topic", "").strip()
                if action == "add" and topic:
                    result = await asyncio.to_thread(add_monitor, topic)
                elif action == "remove" and topic:
                    result = await asyncio.to_thread(remove_monitor, topic)
                elif action == "list":
                    topics = await asyncio.to_thread(list_monitors)
                    result = ("Monitoring: " + ", ".join(topics)) if topics else "No topics are being monitored."
                else:
                    result = "Specify action (add/remove/list) and a topic."

            elif name == "shutdown_jarvis":
                self.ui.write_log("SYS: Shutdown requested.")
                async def _do_shutdown():
                    await self._save_session_summary()
                    if self.session:
                        try:
                            await self.session.send_client_content(
                                turns={"role": "user", "parts": [{"text": "Say a brief natural goodbye to the user."}]},
                                turn_complete=True,
                            )
                        except Exception:
                            pass
                    await asyncio.sleep(1.5)
                    self.ui.stop_tray()
                    import os as _os
                    _os._exit(0)
                asyncio.create_task(_do_shutdown())

            elif self._action_registry.has(name):
                # file_processor: fall back to the currently-uploaded file when none is given
                if name == "file_processor" and not args.get("file_path") and self.ui.current_file:
                    args["file_path"] = self.ui.current_file
                _ctx = {"player": self.ui, "speak": self.speak,
                        "response": None, "session_memory": self._session_mem()}
                # The policy's timeout: a hung handler must not hold the
                # conversation hostage. The worker thread itself cannot be
                # killed and will finish (or fail) on its own — the model
                # gets its turn back either way.
                r = await asyncio.wait_for(
                    loop.run_in_executor(
                        None, lambda: self._action_registry.run(name, args, _ctx)),
                    timeout=policy.timeout)
                result = r or "Done."
                # M9: one content surface — the panel, not a per-tool
                # special case. Runs before the M8 note is appended, so the
                # user reads the tool's own answer, not the model's verdict.
                self._surface_content(name, args, r)

            else:
                if self._plugin_registry.has(name):
                    r = await asyncio.wait_for(
                        loop.run_in_executor(
                            None,
                            lambda: self._plugin_registry.run(
                                name, args, player=self.ui,
                                session_memory=self._session_mem())),
                        timeout=policy.timeout)
                    result = r or "Done."
                    self._surface_content(name, args, r)
                else:
                    result = f"Unknown tool: {name}"

        except asyncio.TimeoutError:
            _failed = True
            result = f"Tool '{name}' timed out after {policy.timeout}s."
            print(f"[JARVIS] ✖ {name} exceeded its {policy.timeout}s budget")
            self.speak_error(name, f"timed out after {policy.timeout}s")
        except Exception as e:
            _failed = True
            result = f"Tool '{name}' failed: {e}"
            traceback.print_exc()
            self.speak_error(name, e)

        # M8: verify before yielding the turn. A crashed/timed-out dispatch
        # already told the model the truth — verifying it would only add a
        # second opinion about an event that did not happen.
        if not _failed and policy.verification == "verifier":
            # VERIFYING lights exactly while a real check runs — never as
            # decoration; muted HUDs stay untouched like every other state.
            if not self.ui.muted:
                self.ui.set_state("VERIFYING")
            result, verdict = await self._run_verification(name, args, result)

        # M11: the bus hears every turn of the wheel — success and failure
        # alike feed the dashboard stream (§3.15); the prints stay as-is.
        # Verdict rides the bus when one exists: the machine-readable
        # twin of the prose note, so M5/M6 tracing can later count
        # real pass/fail/skip outcomes instead of sniffing prose.
        _evt_data = {"ok": not _failed, "result": str(result)[:200]}
        if verdict is not None:
            _evt_data["verification"] = verdict
        self.emit_event(
            EventType.TOOL_ERROR if _failed else EventType.TOOL_RESULT,
            source=name,
            data=_evt_data,
            priority=Priority.HIGH,
        )

        if not self.ui.muted:
            # A captured frame queued for the model is an observation still in
            # flight — show it until the flush hands it over; otherwise back to
            # LISTENING. getattr: test doubles built via __new__ carry neither
            # attribute, and the (no-session) case could never flush the frame
            # — it must not stick on OBSERVING forever.
            self.ui.set_state(
                "OBSERVING"
                if (getattr(self, "_pending_vision", None)
                    and getattr(self, "session", None))
                else "LISTENING")

        print(f"[JARVIS] 📤 {name} → {str(result)[:80]}")

        # A tool that declared itself NON_BLOCKING also says when its answer may
        # re-enter the conversation. Without this the model finishes whatever it
        # was saying and then reads the result out on top of it — which, for
        # something like a phone call already ringing, is exactly the noise the
        # non-blocking call was meant to avoid. Tools that declared nothing get
        # the API default and behave as they always have.
        _sched = (self._action_registry.scheduling(name)
                  or self._plugin_registry.scheduling(name))
        _extra = {"scheduling": _sched} if _sched else {}
        response = {"result": result}
        if verdict is not None:
            # Machine-readable twin of the prose note above: the automation
            # adapter reads this to refuse a failed verification instead of
            # guessing from failure-shaped strings.
            response["verification"] = verdict
        return types.FunctionResponse(
            id=fc.id, name=name,
            response=response,
            **_extra
        )

    async def _run_verification(self, name: str, args: dict, result):
        """Run the Verifier for `name` and return (text, verdict).

        text = the tool's result plus the model-facing prose note (what the
        model reads); verdict = the structured twin for non-model consumers
        (the automation adapter refuses a failed verdict instead of sniffing
        failure-shaped strings). Bounded by VERIFY_TIMEOUT_S: a probe stuck
        on a wedged process list or browser must not hold the conversation —
        on timeout the note says the check never finished rather than
        guessing at its answer. Runs in an executor because the probes are
        synchronous OS/playwright calls.
        """
        loop = asyncio.get_event_loop()
        try:
            vres = await asyncio.wait_for(
                loop.run_in_executor(
                    None, lambda: VERIFIER.verify(name, args, result)),
                timeout=VERIFY_TIMEOUT_S)
        except asyncio.TimeoutError:
            vres = VerificationResult(
                status=VerificationStatus.UNVERIFIABLE, confidence=0.0,
                message=f"verification timed out after {VERIFY_TIMEOUT_S:g}s")
        except Exception as e:
            vres = VerificationResult(
                status=VerificationStatus.UNVERIFIABLE, confidence=0.0,
                message=f"verification crashed: {e}")
        print(f"[Verify] {name}: {vres.status.value} "
              f"(confidence {vres.confidence:.2f}) — {vres.message}")
        return (f"{result}{verification_note(vres)}", verdict_dict(vres, name))

    def _surface_content(self, name: str, args: dict, result) -> None:
        """M9: the single route from a tool result to the content panel.

        web_search keeps its mode/query label exactly as before; declared
        actions and plugins surface only reference-shaped results (the
        length floor keeps "Opened X." in the voice where it belongs).
        Called BEFORE the M8 verification note is appended: what the user
        reads is the tool's own answer — the verdict is the model's
        contract, not theirs. A display failure never breaks dispatch.
        """
        if not result:
            return
        text = str(result)
        if text.startswith(_CONTENT_SKIP):
            return
        if name == "web_search":
            mode  = args.get("mode", "search")
            query = args.get("query") or ", ".join(args.get("items", []))
            label = f"{mode.upper()} — {query[:38]}" if query else mode.upper()
        elif name in _CONTENT_SURFACE:
            if len(text) < _CONTENT_MIN_CHARS:
                return
            label = _CONTENT_SURFACE[name]
        elif self._plugin_registry.has(name):
            if len(text) < _CONTENT_MIN_CHARS:
                return
            label = name.upper().replace("_", " ")
        else:
            return
        try:
            self.ui.show_content(label, text)
        except Exception:
            pass

    async def _send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            # Gemini 3.x Live rejects the old realtime_input.media_chunks field
            # (what `media=...` maps to) and closes the socket with a 1007. Send
            # mic / phone PCM through the new `audio` field instead. Queue items
            # are {"data": <bytes>, "mime_type": <str>} from _listen_audio and
            # the phone relay.
            await self.session.send_realtime_input(
                audio=types.Blob(
                    data=msg["data"],
                    mime_type=msg.get("mime_type", "audio/pcm"),
                )
            )

    def _pc_mic_blocked(self) -> str:
        """M9: why the PC microphone must not stream right now ("" = it may).

        The mute path in one named, testable place instead of an inline
        condition nobody could assert: `ui.muted` (the user hit mute — video
        playing, a private moment) and `_phone_active` (the phone relay owns
        the mic) both silence the PC mic before a byte is queued. Returns
        the reason, so a test can say WHY the mic was held, not only that
        something was.
        """
        if self.ui.muted:
            return "muted"
        if self._phone_active:
            return "phone"
        return ""

    async def _listen_audio(self):
        print("[JARVIS] 🎤 Mic started")
        loop = asyncio.get_event_loop()

        def callback(indata, frames, time_info, status):
            # ── Wake-word gate ───────────────────────────────────────────────
            # While asleep, the mic audio NEVER goes to Gemini (nothing is
            # streamed, so JARVIS can't respond to speech not addressed to it and
            # nothing leaves the machine). Frames are instead handed to the local
            # detector, which runs its model in ITS OWN thread — the cost here is
            # only a queue push, so the audio path is never slowed. When wake word
            # is off (default) or we're awake, this is a single boolean check.
            if self._wake_enabled and not self._awake:
                det = self._wake_detector
                if det is not None:
                    det.feed(indata)
                return
            with self._speaking_lock:
                jarvis_speaking = self._is_speaking

            # ── Barge-in ─────────────────────────────────────────────────────
            # While JARVIS talks the mic is not streamed, but it is still worth
            # listening to locally: if the user starts speaking, cut the answer
            # short the way a person would stop when interrupted.
            #
            # The whole difficulty is echo — on speakers the mic hears JARVIS.
            # So the test is not "is the mic loud" but "is the mic louder than
            # the echo of what we are playing right now", sustained long enough
            # that a cough or a keystroke cannot trigger it.
            if jarvis_speaking:
                # Nothing is streamed while JARVIS talks.
                #
                # Barge-in: classify each mic block through the EchoGuard.
                # After `required_blocks` of sustained "this is a real voice,
                # not our echo", cut the answer short — the way a person stops
                # when interrupted.
                try:
                    if self._echo.is_user_speech(
                            indata, SEND_SAMPLE_RATE, _pcm_level(indata)):
                        self._barge_blocks += 1
                        if self._barge_blocks >= self._echo.required_blocks:
                            self._barge_blocks = 0
                            self.interrupt()
                    else:
                        self._barge_blocks = 0
                except Exception:
                    pass
                return

            # ── Echo tail ────────────────────────────────────────────────────
            # The speaking flag has dropped but the speakers have not finished.
            # Sending this to the model is how an assistant hears itself, decides
            # it was addressed, and answers its own last sentence. The microphone
            # stays OPEN — the guard only drops blocks that are our own voice, so
            # replying the instant it stops still works.
            if self._tail_active():
                try:
                    if not self._echo.is_user_speech(
                            indata, SEND_SAMPLE_RATE, _pcm_level(indata)):
                        return
                    self._tail_until = 0.0      # a real voice ends the tail early
                except Exception:
                    return
            elif self._echo._hist:
                self._echo.reset()

            # ── Push-to-talk ─────────────────────────────────────────────────
            # When it is on the microphone is closed by default and the chord
            # opens it, which is the whole point: nothing leaves the machine
            # unless you are holding the key.
            if self._ptt_enabled and not self._ptt_held:
                return

            # M9: the send decision itself is one named call — muted or
            # phone-owned, and not one byte leaves the mic.
            if not self._pc_mic_blocked():
                data = indata.tobytes()
                loop.call_soon_threadsafe(
                    self.out_queue.put_nowait,
                    {"data": data, "mime_type": "audio/pcm"}
                )
                # Feed the live mic level to the HUD so the waveform reacts to
                # the user's actual voice while listening. Purely cosmetic — any
                # failure here must never disturb the mic.
                try:
                    self.ui.set_audio_level(_pcm_level(indata))
                except Exception:
                    pass

        try:
            def _open_mic(dev):
                return sd.InputStream(
                    samplerate=SEND_SAMPLE_RATE,
                    channels=CHANNELS,
                    dtype="int16",
                    blocksize=CHUNK_SIZE,
                    device=dev,
                    callback=callback,
                )

            # Which microphone. resolve() returns None for "system default" and
            # for a saved device that is no longer present — so a headset
            # unplugged since the last run falls back to the built-in mic
            # instead of raising on startup and taking the session with it.
            _mic_name = get_input_device()
            _mic_dev  = audio_devices.resolve(_mic_name, "input")
            if _mic_dev is not None:
                print(f"[JARVIS] 🎤 Input device: {_mic_name}")
            try:
                _mic_stream = _open_mic(_mic_dev)
            except Exception as _e:
                # A device the picker listed but the driver will not open right
                # now — exclusive mode, a webcam already in use, a virtual mic
                # whose source went away. Chosen hardware failing must never
                # mean the assistant cannot hear at all.
                if _mic_dev is None:
                    raise
                print(f"[JARVIS] ⚠️  Mic '{_mic_name}' failed: {_e} — using default")
                self.ui.write_log(
                    f"SYS: Microphone '{_mic_name}' unavailable — using system default."
                )
                _mic_stream = _open_mic(None)

            with _mic_stream:
                print("[JARVIS] 🎤 Mic stream open")
                while True:
                    await asyncio.sleep(0.1)
        except Exception as e:
            print(f"[JARVIS] ❌ Mic: {e}")
            raise

    def _session_mem(self):
        """The session-lifetime handoff slot, created on first dispatch.

        Lazily: test doubles built via ``__new__`` skip ``__init__`` and must
        still dispatch without AttributeError (the slot simply didn't exist
        for them before it was wired).
        """
        sm = getattr(self, "session_memory", None)
        if sm is None:
            sm = SessionMemory()
            try:
                self.session_memory = sm
            except Exception:
                pass
        return sm

    async def _flush_pending_vision(self) -> bool:
        """Send a captured frame immediately after its tool response.

        The frame is already in hand by the time `screen_process` returns — the
        capture happened inside the tool call. The old flow still made the model
        speak a turn first and only injected the image on that turn's
        turn_complete, which cost a whole extra round trip AND produced two
        spoken answers: one improvised without the picture, then the real one.
        Sending it here means the model has the tool result and the image before
        it generates anything, so the user gets one answer, sooner.
        """
        if not (self._pending_vision and self.session):
            return False

        import base64 as _b64
        img_b, mime_t, question, angle = self._pending_vision
        self._pending_vision = None
        b64 = _b64.b64encode(img_b).decode("ascii")
        print(f"[Vision] 📤 {len(img_b):,} bytes (angle={angle}) → main session")

        # Label the source through the one choke point (core.vision_labels).
        # Without it the image arrives carrying nothing but the user's own
        # sentence, and a screenshot of this app - which has a face in the
        # middle of it - got read as a photo of the user. What each label
        # *means* is explained once, in the generated [SELF] block.
        src = source_text(CAMERA if angle == "camera" else SCREEN, question)
        await self.session.send_client_content(
            turns={"role": "user", "parts": [
                {"inline_data": {"mime_type": mime_t, "data": b64}},
                {"text": src},
            ]},
            turn_complete=True,
        )

        if self._vision_cam_active:
            # Camera: stay busy until JARVIS has finished speaking the answer,
            # then close the preview.
            self._vision_cam_active    = False
            self._vision_close_pending = True
        else:
            self._vision_busy = False
        return True

    async def _flush_memory_notes(self) -> bool:
        """Inject memory written mid-session into the live context.

        Without this the model only sees memory captured at session start, so a
        fact it just saved stays invisible until the next reconnect — the exact
        'memory storage with no retrieval' gap. Sent right after the tool
        response (same safe post-tool point as the vision frame), bounded to
        one message per flush.
        """
        if not (self._memory_notes and self.session):
            return False
        notes = self._memory_notes[:]
        self._memory_notes.clear()
        text = ("SYS (memory): saved this session — " +
                "; ".join(notes[:5]) +
                ("\n…" if len(notes) > 5 else ""))
        await self.session.send_client_content(
            turns={"role": "user", "parts": [{"text": text}]},
            turn_complete=True,
        )
        print(f"[Memory] 📤 injected {len(notes)} memory note(s) into live context")
        return True

    async def _receive_audio(self):
        print("[JARVIS] 👂 Recv started")
        out_buf, in_buf = [], []

        try:
            while True:
                async for response in self.session.receive():

                    # ── Session resumption ───────────────────────────────────
                    # The server sends this periodically; only resumable
                    # handles are kept (see _note_resumption_update).
                    self._note_resumption_update(
                        getattr(response, "session_resumption_update", None))

                    if response.data:
                        if self._interrupted:
                            pass  # discard: interrupted
                        else:
                            if self._turn_done_event and self._turn_done_event.is_set():
                                self._turn_done_event.clear()
                            # Split into ~50 ms chunks so interrupt() stops audio within 50 ms
                            # (24000 Hz × 2 bytes/sample × 0.05 s = 2400 bytes per slice)
                            _audio_data = response.data
                            _SLICE = 2400
                            for _i in range(0, len(_audio_data), _SLICE):
                                self.audio_in_queue.put_nowait(_audio_data[_i : _i + _SLICE])

                    if response.server_content:
                        sc = response.server_content

                        if sc.output_transcription and sc.output_transcription.text:
                            txt = _clean_transcript(sc.output_transcription.text)
                            # First words of a response: the model is composing
                            # its answer — a real THINKING moment, until audio
                            # or a tool call takes over the state.
                            if txt and not out_buf and not self.ui.muted:
                                self.ui.set_state("THINKING")
                            # A turn that involves a tool call passes through
                            # several turn_completes, and the API re-sends the
                            # tail of the transcript across them. Comparing only
                            # against the previous chunk missed that — once
                            # out_buf had been flushed and emptied, the repeat
                            # sailed straight back in, which logged the answer
                            # twice AND made the avatar mouth it twice.
                            if txt and not _is_repeat_chunk(txt, out_buf):
                                out_buf.append(txt)
                                # Hand the words to the mouth as they arrive, so
                                # the avatar can form the consonants the audio
                                # alone cannot show. Pure string work — it adds
                                # nothing measurable to the response path.
                                self._visemes.feed_text(txt)

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = _clean_transcript(sc.input_transcription.text)
                            if txt:
                                in_buf.append(txt)
                                self._last_user_speech = time.monotonic()
                                self._heard_user_text = True
                                # Every word the server heard from our mic,
                                # logged as it arrives — phantom speech (echo,
                                # TV, room noise) must be visible in the log,
                                # not only at turn-complete where a discarded
                                # turn takes it to the grave with it.
                                print(f"[Audio] user speech: {txt!r}",
                                      flush=True)

                        if sc.turn_complete:
                            if self._turn_done_event:
                                self._turn_done_event.set()

                            # If this turn_complete ends an interrupted response, clear the
                            # flag and skip all further processing for that turn.
                            if self._interrupted:
                                self._interrupted = False
                                in_buf  = []
                                out_buf = []
                                self._visemes.reset()
                                continue

                            full_in = " ".join(in_buf).strip()
                            if full_in:
                                self._last_out_logged = ""   # new exchange
                                # The user has finished — the utterance is in
                                # hand and being understood. Real event, not a
                                # timer: THINKING follows when the answer
                                # starts arriving, SPEAKING when it is voiced.
                                if not self.ui.muted:
                                    self.ui.set_state("UNDERSTANDING")
                                self.ui.write_log(f"You: {full_in}")
                                self._session_log.append(f"User: {full_in}")
                                if self._dashboard:
                                    asyncio.create_task(self._dashboard.broadcast({
                                        "type": "log", "speaker": "user",
                                        "text": full_in,
                                        "ts": datetime.now().isoformat(),
                                    }))

                                # ── Voice persona switch intercept ────────
                                # If the user said a persona switch command,
                                # handle it immediately and suppress the LLM
                                # response for this turn.
                                _vp_id = self._detect_persona_switch(full_in)
                                if _vp_id:
                                    in_buf = []
                                    out_buf = []
                                    self._visemes.reset()
                                    self.interrupt()
                                    self._on_persona_change(_vp_id)
                                    continue

                            in_buf = []

                            full_out = " ".join(out_buf).strip()
                            # Second line of defence: even if a repeat slips
                            # into a *fresh* buffer after a flush, never log the
                            # same answer (or a tail of it) twice in a row.
                            if full_out and len(full_out) >= _REPEAT_MIN and self._last_out_logged:
                                if full_out in self._last_out_logged:
                                    full_out = ""
                            if full_out:
                                self._last_out_logged = full_out
                                self.ui.write_log(f"{self._asst_name}: {full_out}")
                                self._session_log.append(f"{self._asst_name}: {full_out}")
                                if self._dashboard:
                                    asyncio.create_task(self._dashboard.broadcast({
                                        "type": "log", "speaker": "jarvis",
                                        "text": full_out,
                                        "ts": datetime.now().isoformat(),
                                    }))
                            out_buf = []

                            if self._vision_close_pending:
                                # This turn_complete IS the vision answer — close camera + release busy flag
                                self._vision_close_pending = False
                                self._vision_busy = False
                                async def _cam_close():
                                    await asyncio.sleep(2.0)
                                    self.ui.stop_camera_stream()
                                asyncio.create_task(_cam_close())

                    if response.tool_call:
                        fn_responses = []
                        for fc in response.tool_call.function_calls:
                            print(f"[JARVIS] 📞 {fc.name}")
                            fr = await self._execute_tool(fc)
                            fn_responses.append(fr)
                        await self.session.send_tool_response(
                            function_responses=fn_responses
                        )
                        await self._flush_pending_vision()
                        await self._flush_memory_notes()
        except Exception as e:
            print(f"[JARVIS] ❌ Recv: {e}")
            traceback.print_exc()
            raise

    async def _play_audio(self):
        print("[JARVIS] 🔊 Play started")

        _spk_name = get_output_device()
        _spk_dev  = audio_devices.resolve(_spk_name, "output")
        if _spk_dev is not None:
            print(f"[JARVIS] 🔊 Output device: {_spk_name}")

        def _open_spk(dev):
            st = sd.RawOutputStream(
                samplerate=RECEIVE_SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=CHUNK_SIZE,
                device=dev,
            )
            st.start()
            return st

        try:
            stream = _open_spk(_spk_dev)
        except Exception as _e:
            # A chosen output that the host API accepts by name but refuses to
            # open (exclusive mode, wrong sample rate, device asleep) must not
            # cost the user their voice. Fall back to the default and say so.
            if _spk_dev is None:
                raise
            print(f"[JARVIS] ⚠️  Output device '{_spk_name}' failed: {_e} — using default")
            self.ui.write_log(f"SYS: Speaker '{_spk_name}' unavailable — using system default.")
            stream = _open_spk(None)

        # Ask the device how far behind the speakers actually are, rather than
        # assuming. This is what the echo tail is sized from, so a machine with a
        # large audio buffer gets a correspondingly longer guard — and one with a
        # tiny buffer is not penalised with a delay it does not need.
        try:
            lat = float(getattr(stream, "latency", 0.0) or 0.0)
            if 0.0 < lat < 1.0:
                self._out_latency = lat
            print(f"[JARVIS] 🔊 Output latency {self._out_latency*1000:.0f} ms "
                  f"→ echo tail {(self._out_latency + _TAIL_MARGIN)*1000:.0f} ms")
        except Exception:
            pass

        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        self.audio_in_queue.get(),
                        timeout=0.1
                    )
                except asyncio.TimeoutError:
                    if (
                        self._turn_done_event
                        and self._turn_done_event.is_set()
                        and self.audio_in_queue.empty()
                    ):
                        self.set_speaking(False)
                        self._turn_done_event.clear()
                    continue

                self.set_speaking(True)

                # Batch all immediately-available chunks into one write to reduce
                # thread-pool round-trips (was one asyncio.to_thread per 50ms slice).
                # Cap at ~200 ms so interrupt() still stops audio within ~200 ms.
                batch = bytearray(chunk)
                while len(batch) < 9600:   # 9600 bytes ≈ 200 ms at 24 kHz / 16-bit mono
                    try:
                        batch.extend(self.audio_in_queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break

                # Drive the HUD waveform and the avatar's mouth from JARVIS's
                # own voice. The batch is up to 200 ms long, so we hand over a
                # *schedule* of 20 ms viseme frames instead of a single averaged
                # level and let the HUD play it out in step with the audio.
                try:
                    pcm = np.frombuffer(bytes(batch), dtype=np.int16)
                    hop = _VIS_HOP / RECEIVE_SAMPLE_RATE
                    frames = _pcm_visemes(pcm, sr=RECEIVE_SAMPLE_RATE)
                    # When does this batch become audible? The stream was
                    # started at launch and its callback has been pulling
                    # silence ever since, so the first bytes of a reply reach
                    # the speaker about one callback period later — NOT one
                    # buffer later. `stream.latency` reports the buffer's
                    # capacity, which is how much can be queued ahead, and on
                    # Windows that is commonly 300-500 ms. Anchoring on it put
                    # the entire schedule a buffer late; that is the half second
                    # of lag, and it grew with whatever the device reported.
                    #
                    # After the anchor nothing needs measuring: the device
                    # consumes at exactly realtime, so each batch sounds one
                    # batch-duration after the one before it. The cursor is
                    # re-anchored only when it leaves the range physically
                    # possible — behind `now` means the device drained and this
                    # batch starts a fresh stretch of speech, while further
                    # ahead than the buffer can hold means it has drifted.
                    now = time.time()
                    horizon = self._out_latency + _CURSOR_SLACK
                    if not (now <= self._play_cursor <= now + horizon):
                        self._play_cursor = now + _FIRST_SOUND
                    at = self._play_cursor
                    # Advance by the batch's own duration whether or not it
                    # yielded frames, so a block too short to analyse cannot
                    # shift everything after it out of step with the audio.
                    self._play_cursor += pcm.size / RECEIVE_SAMPLE_RATE
                    if frames:
                        frames = self._visemes.frames(frames, hop)
                        self.ui.push_visemes(frames, hop, at)
                        # Barge-in needs to know what we are playing, not just
                        # how loud: the guard subtracts this from the microphone.
                        self._out_level = max(f[0] for f in frames)
                        self._echo.note_output(pcm, RECEIVE_SAMPLE_RATE,
                                               self._out_level)
                    else:
                        lvl = _pcm_level(pcm)
                        self.ui.set_audio_level(lvl)
                        self._out_level = lvl
                        self._echo.note_output(pcm, RECEIVE_SAMPLE_RATE, lvl)
                except Exception:
                    pass

                try:
                    await asyncio.to_thread(stream.write, bytes(batch))
                except (RuntimeError, asyncio.CancelledError):
                    break   # executor shutting down — exit cleanly
        except Exception as e:
            print(f"[JARVIS] ❌ Play: {e}")
            raise
        finally:
            self.set_speaking(False)
            stream.stop()
            stream.close()

    # ── Morning briefing ────────────────────────────────────────────────────────

    async def _send_startup_briefing(self) -> None:
        """
        Two-phase briefing optimized for speed:
          Phase 1 — instant greeting (no tools) → speech starts in <1s
          Phase 2 — news pre-fetched in a background thread while Phase 1 plays,
                    delivered as ready text (no Gemini tool-call round-trip) and
                    shown on the UI content panel. Waits for turn_complete event
                    instead of a fixed sleep so there is no unnecessary gap.
        """
        memory   = load_memory()
        identity = memory.get("identity", {})

        def _val(k: str) -> str:
            e = identity.get(k, {})
            return (e.get("value", "") if isinstance(e, dict) else str(e)).strip()

        lang = _val("language")
        name = _val("name")
        time_str = datetime.now().strftime("%H:%M")

        # Start fetching news immediately — runs in parallel while phase 1 plays
        loop = asyncio.get_event_loop()
        news_future = loop.run_in_executor(None, _fetch_news_sync, "top world news today")

        await asyncio.sleep(0.3)
        if not self.session:
            return

        # ── Phase 1: instant greeting ─────────────────────────────────────────
        # The briefing fires before the user has said anything, so the
        # remembered language is the only signal there is. It is a starting
        # point, not a setting: the moment they reply, their language wins.
        lang_clause = (f" Speak this greeting in {lang}, then follow the "
                       f"user's own language from their first reply onward."
                       if lang else "")
        name_clause = f" Address the user as {name}." if name else ""

        # Inject last session context if available — pop removes it so it's never repeated
        last = await asyncio.to_thread(pop_last_session)
        session_clause = ""
        if last:
            try:
                _delta = (datetime.now() - datetime.strptime(last["date"], "%Y-%m-%d")).days
                _when  = "earlier today" if _delta == 0 else ("yesterday" if _delta == 1 else f"{_delta} days ago")
            except Exception:
                _when = "last time"
            session_clause = (
                f" Also briefly and naturally mention that {_when}: {last['summary']}"
            )

        p1 = (
            f"Greet the user warmly, mention it is {time_str}, and say you are fetching today's news now.{session_clause} "
            f"Keep it to 2 short sentences max. Do not call any tools.{lang_clause}{name_clause}"
        )

        # Clear the turn-done event so we can wait for Phase 1 to finish
        if self._turn_done_event:
            self._turn_done_event.clear()

        await self.session.send_client_content(
            turns={"role": "user", "parts": [{"text": p1}]},
            turn_complete=True,
        )
        print("[JARVIS] Briefing phase 1 (greeting) sent.")

        # ── Phase 2: fire as soon as Phase 1 audio is done ───────────────────
        async def _deliver_news():
            try:
                lang_str = (f" Speak in {lang} unless the user has since "
                            f"spoken another language, in which case use theirs."
                            if lang else "")

                # Wait for news fetch (already running) and Phase 1 turn-complete
                # in parallel — whichever takes longer determines the wait time
                news_done   = asyncio.wrap_future(news_future)
                turn_waited = False
                if self._turn_done_event:
                    try:
                        await asyncio.wait_for(self._turn_done_event.wait(), timeout=6.0)
                        turn_waited = True
                    except asyncio.TimeoutError:
                        pass

                # Extra buffer: turn_complete fires when Gemini finishes *generating*
                # Phase 1, but audio may still be playing.  Waiting a beat here
                # prevents Phase 2 audio from arriving while Phase 1 is mid-sentence
                # (which sounds like a "repeated first response" to the user).
                if turn_waited:
                    await asyncio.sleep(0.8)
                else:
                    await asyncio.sleep(1.0)

                try:
                    news_text = await asyncio.wait_for(news_done, timeout=8.0)
                except Exception as e:
                    self.ui.write_log(f"SYS: News fetch timed out/failed: {e!r}")
                    news_text = ""

                if not self.session:
                    return

                failed = (not news_text) or news_text.startswith(
                    ("No news found", "Search failed", "Please provide")
                )
                if not failed:
                    # Show on UI content panel immediately
                    self.ui.show_content("NEWS — top world news today", news_text)

                    p2 = (
                        f"[BRIEFING] Here are today's top news headlines:\n{news_text}\n\n"
                        "Pick ONE headline, summarise it in one sentence, then say the full list "
                        f"is displayed on screen. Do not call any tools.{lang_str}"
                    )
                else:
                    self.ui.write_log(
                        f"SYS: News unavailable — backend returned: {news_text[:120]!r}"
                    )
                    p2 = (
                        "News headlines could not be fetched right now. "
                        f"Let the user know briefly.{lang_str}"
                    )

                await self.session.send_client_content(
                    turns={"role": "user", "parts": [{"text": p2}]},
                    turn_complete=True,
                )
                print("[JARVIS] Briefing phase 2 (news) sent.")
            except Exception as e:
                print(f"[Briefing] Phase 2 error: {e}")
                print(f"[JARVIS] Briefing phase 2 failed: {e}")
                self.ui.write_log("SYS: Could not fetch the news for the briefing.")

        asyncio.create_task(_deliver_news())

    # ── Session memory ──────────────────────────────────────────────────────────

    async def _save_session_summary(self) -> None:
        """Summarise the current session in 1-2 sentences and save to long_term.json."""
        log = self._session_log
        if len(log) < 3:          # need at least one exchange to be worth saving
            return

        memory = load_memory()
        lang_entry = memory.get("identity", {}).get("language", {})
        lang = (lang_entry.get("value", "") if isinstance(lang_entry, dict) else str(lang_entry)).strip()
        lang = lang or "English"

        # Persist the full transcript before summarising
        save_session_log(list(log), language=lang)

        self._session_log = []    # reset immediately so the next session starts clean

        convo = "\n".join(log[-40:])   # cap at last 40 turns to stay within token budget
        prompt = (
            f"Summarize this conversation in 1-2 sentences in {lang}. "
            "Focus on what the user accomplished or discussed. "
            "Output ONLY the summary text, nothing else:\n\n" + convo
        )
        try:
            from core import gemini
            summary = await asyncio.to_thread(
                gemini.text, prompt, gemini.SMART, None, 30_000,
            )
            if summary:
                save_session_summary(summary, lang)
        except Exception as e:
            print(f"[Memory] ⚠️ Session summary failed: {e}")

    # ── System monitor ──────────────────────────────────────────────────────────

    async def _run_system_monitor(self) -> None:
        """Background task: voice alerts when metrics exceed thresholds."""
        while True:
            await asyncio.sleep(10)
            alert = await asyncio.to_thread(self._sys_monitor.check)
            if not alert or not self.session or not self._awake:
                continue
            # Don't interrupt an active conversation
            with self._speaking_lock:
                speaking = self._is_speaking
            if speaking or (time.monotonic() - self._last_user_speech) < 10:
                continue
            try:
                await self.session.send_client_content(
                    turns={"role": "user", "parts": [{"text": alert}]},
                    turn_complete=True,
                )
            except Exception as e:
                print(f"[Monitor] ⚠️ Could not send alert: {e}")

    # ── Background monitor ──────────────────────────────────────────────────────

    async def _run_background_monitor(self) -> None:
        """Check user-configured topics once per day; speak alerts when new headlines appear."""
        await asyncio.sleep(300)          # wait 5 min after startup before first check
        while True:
            if self.session and self._awake:
                # Don't interrupt if user spoke recently or JARVIS is mid-sentence
                with self._speaking_lock:
                    speaking = self._is_speaking
                recent_speech = (time.monotonic() - self._last_user_speech) < 30
                if not speaking and not recent_speech:
                    try:
                        alerts = await asyncio.to_thread(monitor_check_all)
                        memory = load_memory()
                        lang_e = memory.get("identity", {}).get("language", {})
                        lang   = (lang_e.get("value", "") if isinstance(lang_e, dict) else str(lang_e)).strip() or "English"
                        for alert in alerts:
                            msg = (
                                f"{alert}\n\n"
                                f"Inform the user about this development naturally in {lang}. "
                                "One brief sentence only."
                            )
                            await self.session.send_client_content(
                                turns={"role": "user", "parts": [{"text": msg}]},
                                turn_complete=True,
                            )
                            print("[JARVIS] Monitor alert sent.")
                            await asyncio.sleep(6)   # gap between consecutive alerts
                    except Exception as e:
                        print(f"[Monitor] ⚠️ Background check error: {e}")
            await asyncio.sleep(1800)     # check every 30 minutes

    # ── Proactive mode ──────────────────────────────────────────────────────────

    async def _run_proactive_mode(self) -> None:
        """
        Background task: periodically checks if the user has been silent long enough,
        then hands time + memory context to Gemini so it can decide what (if anything)
        to say proactively. No hardcoded rules — Gemini makes the call.
        """
        while True:
            await asyncio.sleep(60)   # evaluate once per minute

            if not self.session or not self._awake:
                continue

            with self._speaking_lock:
                speaking = self._is_speaking
            if speaking:
                continue

            if not self._proactive.should_trigger(self._last_user_speech):
                continue

            self._proactive.mark_triggered()

            try:
                memory       = await asyncio.to_thread(load_memory)
                monitors     = await asyncio.to_thread(list_monitors)
                recent_turns = self._session_log[-8:] if self._session_log else []
                prompt = self._proactive.build_prompt(
                    memory       = memory,
                    monitors     = monitors or None,
                    recent_turns = recent_turns or None,
                )
                await self.session.send_client_content(
                    turns={"role": "user", "parts": [{"text": prompt}]},
                    turn_complete=True,
                )
                print("[JARVIS] Proactive check-in.")
            except Exception as e:
                print(f"[Proactive] ⚠️ {e}")

    # ── Phone audio relay ────────────────────────────────────────────────────────

    async def _relay_phone_audio(self) -> None:
        """Forward phone mic PCM chunks from dashboard queue into the Gemini Live session."""
        q = self._dashboard._phone_audio_queue
        while True:
            try:
                chunk = await asyncio.wait_for(q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                # No audio for 1 s → phone mic inactive, give PC mic back
                self._phone_active = False
                continue
            self._phone_active = True   # phone is streaming — silence PC mic
            with self._speaking_lock:
                speaking = self._is_speaking
            if not speaking and not self.ui.muted:
                try:
                    self.out_queue.put_nowait(chunk)
                except asyncio.QueueFull:
                    pass

    def _on_phone_connected(self) -> None:
        self.ui.write_log("SYS: Phone connected via Remote Dashboard.")
        self.ui.notify_phone_connected()

    # ── dashboard command relay ─────────────────────────────────────────────

    async def _process_dashboard_commands(self) -> None:
        while True:
            try:
                text = await asyncio.wait_for(
                    self._dashboard._command_queue.get(), timeout=0.5
                )
                if not text:
                    continue
                # Wait up to 8s for session to become ready after a wake
                for _ in range(80):
                    if self.session:
                        break
                    await asyncio.sleep(0.1)
                if self.session:
                    # A remote command is deliberate control and the phone user
                    # has no desktop WAKE button �?" so it wakes JARVIS if asleep.
                    if self._wake_enabled and not self._awake:
                        self.wake(reason="remote command")
                    self._heard_user_text = True
                    print(f"[Input] web command: {text!r}", flush=True)
                    await self.session.send_client_content(
                        turns={"role": "user", "parts": [{"text": text}]},
                        turn_complete=True,
                    )
                    self.ui.write_log(f"[Web]: {text}")
                else:
                    # M14 (§3.6): no session — answer over the REST ladder
                    # instead of dropping the user's words on the floor.
                    # Fail-soft: with the model down too, _offline_reply
                    # returns the honest line and the user still hears back.
                    self.ui.write_log(f"[Web]: {text}")
                    _loop = asyncio.get_running_loop()
                    reply = await _loop.run_in_executor(
                        None, self._offline_reply, text)
                    if reply:
                        self.ui.write_log(f"[Offline] {reply}")
                        await _loop.run_in_executor(
                            None, self._offline_speak, reply)
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                print(f"[Dashboard] Command error: {e}")
                await asyncio.sleep(0.5)

    # ── M11: observability — event bus + dashboard surface (§3.15/§3.16) ──

    def emit_event(self, event_type: EventType, *, source: str = "jarvis",
                   data: dict | None = None,
                   priority: Priority = Priority.NORMAL,
                   correlation_id: str = "") -> bool:
        """Fire-and-forget onto the wired bus. Never raises and never blocks:
        observability must not be able to break the thing it observes. When
        the bus isn't running (early startup, tests built via __new__) the
        event is dropped the way a full queue drops it — and the print()
        logging beside it keeps working regardless (§3.15 shim)."""
        bus = getattr(self, "_event_bus", None)
        if bus is None or not bus.stats.get("running"):
            return False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        evt = Event(priority=priority, event_type=event_type,
                    source=source, data=data or {})
        if correlation_id:
            evt.correlation_id = correlation_id
        loop.create_task(bus.publish(evt))
        return True

    # ── Automation V0.1: engine → notification / event-bus adapters ────────

    def _automation_notify(self, message: str) -> None:
        try:
            from core.notification_manager import get_notification_manager
            get_notification_manager().notify(
                category="automation", title="Automation",
                message=str(message)[:500])
            return
        except Exception:
            pass
        try:
            self.ui.show_content("Automation", str(message)[:4000])
        except Exception:
            pass

    def _automation_emit(self, kind: str, payload: dict, run_id: str) -> None:
        # Phase 12: the activity feed gets run-level lifecycle only —
        # per-node detail would flood it, and it already lives in the run
        # record (config/automations/runs/…) and the automations panel.
        kind = str(kind or "")
        # V2 core (Layer D): the HUD band consumes run AND node kinds over
        # its own queued Qt signal — a UI observer seam, not a second bus.
        # It sits BEFORE the run-only filter below on purpose.
        try:
            _emit = getattr(self.ui, "emit_automation", None)
            if callable(_emit):
                _emit(kind, payload or {}, run_id)
        except Exception:
            pass
        if not (kind.startswith("run_") or kind == "approval_waiting"):
            return
        self.emit_event(EventType.CUSTOM, source="automation",
                        data=dict(payload or {}),
                        correlation_id=str(run_id or ""))

    async def _bridge_event(self, event: Event) -> None:
        """The bus' one subscriber, feeding BOTH §3.15 consumers: the
        activity feed (Phase 32 — the HUD overlay drains it on its own GUI
        timer, so it fills whether or not anything is watching) and the
        dashboard surfaces (the /api/events ring snapshot + live /ws
        push). No dashboard attached is the normal state in tests, never an
        error."""
        feed = getattr(self, "_activity", None)
        if feed is not None:
            try:
                feed.append(event)
            except Exception:
                pass
        dash = getattr(self, "_dashboard", None)
        if not dash:
            return
        try:
            payload = event.to_dict()
            dash.push_event(payload)
            await dash.broadcast({"type": "event", "event": payload},
                                 history=False)
        except Exception:
            pass

    def _dashboard_status(self) -> dict:
        """The runtime's own health, read live for GET /api/status."""
        return {
            "state": self.ui.state,
            "session": bool(self.session),
            "muted": bool(self.ui.muted),
            "phone_active": bool(self._phone_active),
            "event_bus": dict(self._event_bus.stats),
        }

    def emergency_stop(self) -> None:
        """Dashboard emergency-stop (§3.16): mute the mic and flush pending
        input so not one more byte reaches the model. Idempotent (the muted
        setter is a no-op when already muted) and never raises — it arrives
        over HTTP, often with a phone in the user's hand. Audio already in
        the speaker keeps playing; the *input* path is what stops here."""
        self.ui.muted = True                     # real toggle: state + style + log
        q = self.audio_in_queue
        if q is not None:
            try:
                while True:
                    q.get_nowait()
            except asyncio.QueueEmpty:
                pass
        self.emit_event(EventType.USER_INTERRUPT, source="dashboard",
                        data={"kind": "emergency_stop"},
                        priority=Priority.CRITICAL)

    # ── M14: degraded offline voice — the Live API down (§3.6) ─────────────
    # DECIDED at M5, wired at M14: keep core/stt.py + core/tts.py and use them
    # as the fallback when the session is unreachable. Every step is fail-soft —
    # the engines are optional installs, so a missing engine degrades the path
    # again (to text) and says so once instead of failing the whole mode.

    def _offline_engines(self) -> OfflineVoice:
        """Lazy: Whisper/TTS are constructed on first use, never at startup."""
        if getattr(self, "_offline", None) is None:
            self._offline = OfflineVoice(log=self.ui.write_log)
        return self._offline

    def _offline_transcribe(self, pcm) -> str:
        try:
            return self._offline_engines().transcribe(pcm)
        except Exception:
            return ""

    def _offline_reply(self, text: str) -> str:
        """One text turn over the REST model ladder while Live is down.
        Falls to an honest line when the model is unreachable too, so the
        user is never met with silence in a mode whose whole point is that
        something still answers."""
        try:
            from core import gemini
            resp = gemini.call(
                "You are JARVIS. Answer the user briefly, in their language.\n"
                f"User: {text}",
                tier=gemini.FAST,
            )
            out = str(getattr(resp, "text", "") or "").strip()
            if out:
                return out
        except Exception:
            pass
        return ("I can't reach the Live session or the model right now — "
                "I'll keep trying to reconnect.")

    def _offline_speak(self, text: str) -> bool:
        try:
            return self._offline_engines().speak(text)
        except Exception:
            return False

    def note_connection_failure(self) -> bool:
        """Count a failed attempt at the reconnect tail; three in a row with
        no session between them engages degraded mode (§3.6). Returns True
        only on the call that actually engaged it."""
        self._degraded_failures = getattr(self, "_degraded_failures", 0) + 1
        if getattr(self, "_degraded", False) or self.session \
                or self._degraded_failures < 3:
            return False
        self._degraded = True
        self.ui.write_log(
            "SYS: Degraded mode — live session down, offline voice engaged (§3.6)."
        )
        print("[JARVIS] Degraded mode engaged (offline STT/TTS fallback).")
        self.emit_event(EventType.CUSTOM, source="degraded",
                        data={"kind": "degraded_start"},
                        priority=Priority.HIGH)
        try:
            asyncio.get_running_loop().create_task(self._degraded_loop())
        except RuntimeError:
            pass        # called without a loop (tests) — the flag still stands
        return True

    def _exit_degraded(self) -> None:
        """Session is back: drop the flag and the loop closes its own mic
        within one wait-timeout (0.5 s). Called from run() on connect."""
        self._degraded_failures = 0
        if getattr(self, "_degraded", False):
            self._degraded = False
            self.ui.write_log("SYS: Live session back — degraded mode ended.")
            print("[JARVIS] Degraded mode ended (session restored).")
            self.emit_event(EventType.CUSTOM, source="degraded",
                            data={"kind": "degraded_end"},
                            priority=Priority.NORMAL)

    async def _degraded_loop(self) -> None:
        """Mic → EndpointDetector → Whisper → REST reply → offline TTS, while
        the Live API is unreachable. Runs until self._degraded drops or a
        session appears — the mode is a bridge back online, not a second app.
        Any step failing (no engine, no mic, no model) degrades that step to
        text and keeps the loop honest rather than dead."""
        detector = EndpointDetector()
        frames: asyncio.Queue = asyncio.Queue(maxsize=256)
        loop = asyncio.get_running_loop()

        def _offer(item):
            try:
                frames.put_nowait(item)
            except asyncio.QueueFull:
                try:
                    frames.get_nowait()
                except Exception:
                    pass
                try:
                    frames.put_nowait(item)
                except Exception:
                    pass

        def _callback(indata, _frames, _time, _status):
            # PortAudio thread → hop onto the loop; never touch an asyncio
            # queue from a foreign thread, never let a full queue kill audio.
            try:
                data = np.asarray(indata, dtype=np.float32).reshape(-1).copy()
                loop.call_soon_threadsafe(_offer, data)
            except Exception:
                pass

        try:
            stream = sd.InputStream(samplerate=SEND_SAMPLE_RATE, channels=1,
                                    dtype="float32", callback=_callback)
            stream.start()
        except Exception as e:
            self.ui.write_log(f"ERR: Degraded mode needs a microphone: {e}")
            self._degraded = False      # flag ⇔ loop running; retry a later round
            return

        self.ui.write_log("SYS: Degraded listening active — local STT, "
                          "offline replies, offline TTS.")
        try:
            while getattr(self, "_degraded", False) and not self.session:
                try:
                    frame = await asyncio.wait_for(frames.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                utterance = detector.feed(frame)
                if not utterance:
                    continue
                pcm = np.concatenate(utterance)
                text = await loop.run_in_executor(
                    None, self._offline_transcribe, pcm)
                if not text:
                    continue
                self.ui.write_log(f"[Offline]: {text}")
                reply = await loop.run_in_executor(
                    None, self._offline_reply, text)
                if reply:
                    self.ui.write_log(f"[Offline] {reply}")
                    self.emit_event(EventType.CUSTOM, source="degraded",
                                    data={"transcript": text[:160],
                                          "reply": reply[:160]},
                                    priority=Priority.LOW)
                    await loop.run_in_executor(None, self._offline_speak, reply)
        finally:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
            print("[JARVIS] Degraded voice loop ended.")

    # ── main loop ───────────────────────────────────────────────────────────

    async def run(self):
        self._loop = asyncio.get_event_loop()
        self._reconnect_event = asyncio.Event()

        # ── Wire the shared core services to the interface ───────────────────
        # The confirmation gate is useless without a way to ask, and a memory
        # trim is invisible without a way to say so. Both are bound once here
        # rather than passed down through every action signature.
        confirm_gate.bind(
            show = self.ui.show_confirm,
            hide = self.ui.hide_confirm,
            log  = self.ui.write_log,
        )
        set_trim_notifier(self.ui.write_log)

        # Tell the device picker the exact rates the streams open at, from the
        # constants that actually open them — so it can never list a device that
        # cannot be opened at them.
        audio_devices.configure(SEND_SAMPLE_RATE, RECEIVE_SAMPLE_RATE)

        # Enumerate audio devices off-thread. The settings drawer must never pay
        # for host-API enumeration on the Qt thread.
        audio_devices.prefetch()

        # Start dashboard (optional — needs: pip install fastapi "uvicorn[standard]" cryptography)
        try:
            from dashboard.server import DashboardServer
            self._dashboard = DashboardServer()
            self._dashboard.set_connect_callback(self._on_phone_connected)
            self._dashboard.set_status_callback(self._dashboard_status)
            self._dashboard.set_emergency_callback(self.emergency_stop)
            asyncio.create_task(self._dashboard.serve())
            # Runs for the whole lifetime, not just inside an active session
            asyncio.create_task(self._process_dashboard_commands())
        except Exception as e:
            print(f"[Dashboard] Disabled: {e}")
            self._dashboard = None

        # M11: the event bus, finally wired (§3.15) — started before any
        # emission point so early events queue instead of dropping, with one
        # bridge subscriber feeding the dashboard stream. The print() shim
        # stays exactly where it was; the bus rides alongside it.
        await self._event_bus.start()
        self._event_bus.subscribe_all(self._bridge_event)
        # Phase 32: hand the HUD activity overlay its ring (guarded — the
        # overlay pulls from it on a GUI timer; tests carry no real UI).
        _ui = getattr(self, "ui", None)
        _bind_activity = getattr(_ui, "bind_activity_feed", None)
        if callable(_bind_activity):
            try:
                _bind_activity(self._activity)
            except Exception:
                pass
        # Phase 10: the automations panel reads the engine's store directly.
        _bind_auto = getattr(_ui, "bind_automation_store", None)
        if callable(_bind_auto):
            try:
                _bind_auto(self._automation.store
                           if getattr(self, "_automation", None) else None)
            except Exception:
                pass
        # Double-tap on a panel row starts a run through the same engine
        # fast-fail gate the model's run verb uses (nothing bypassed).
        _bind_run = getattr(_ui, "bind_automation_runner", None)
        if callable(_bind_run):
            try:
                _eng = getattr(self, "_automation", None)
                _bind_run((lambda w: _eng.start_run(w, trigger="manual"))
                          if _eng is not None else None)
            except Exception:
                pass
        # V3 Phase 7: the panel's header strip reads the engine's real
        # status_summary() (total/active/running) — never a second poller.
        _bind_eng = getattr(_ui, "bind_automation_engine", None)
        if callable(_bind_eng):
            try:
                _bind_eng(getattr(self, "_automation", None))
            except Exception:
                pass
        self.emit_event(EventType.SYSTEM_STARTUP, source="main")

        # Automation V0.1: scheduler starts once the loop + bus are live and
        # survives reconnects (plain task, outside the session TaskGroup).
        if getattr(self, "_automation", None) is not None:
            try:
                self._automation.refresh_catalog(
                    self._action_registry, self._plugin_registry,
                    INLINE_TOOL_META)
                self._automation.start_ticker()
                print("[Automation] scheduler started")
            except Exception as e:
                print(f"[Automation] scheduler failed: {e}")

        _connect_n = 0
        while True:
            try:
                _connect_n += 1
                print("[JARVIS] Connecting...")
                # Honest states: the first connection is app start-up; any
                # later one means a dropped session is being re-established.
                if _connect_n > 1 or self._resume_handle is not None:
                    self.ui.set_state("RECOVERING")
                else:
                    self.ui.set_state("INITIALISING")
                _resumed_with = self._resume_handle is not None
                config = self._build_config()

                # Fresh client on every reconnect — avoids stale HTTP session state
                # v1alpha carries proactive audio; if it gets rejected we fall
                # back to v1beta.
                client = genai.Client(
                    api_key=_get_api_key(),
                    http_options={"api_version": "v1alpha" if self._enhanced_live else "v1beta"}
                )

                async with (
                    client.aio.live.connect(model=LIVE_MODEL, config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    self.session          = session
                    self.audio_in_queue   = asyncio.Queue()
                    self.out_queue        = asyncio.Queue(maxsize=200)
                    self._turn_done_event = asyncio.Event()

                    # Reset transient state that must not carry over from a previous session
                    self._pending_vision       = None
                    self._vision_cam_active    = False
                    self._vision_close_pending = False
                    self._vision_busy          = False
                    self._vision_last_time     = 0.0
                    self._interrupted          = False

                    print("[JARVIS] Connected.")
                    self._exit_degraded()   # M14: any session up ends the offline bridge
                    if _resumed_with:
                        # Say it plainly: the difference between "it reconnected"
                        # and "it reconnected and still knows what we were doing"
                        # is the whole point, and it is invisible otherwise.
                        self.ui.write_log("SYS: Reconnected — conversation restored.")

                    # Wake word: if enabled, come up ASLEEP (mic gated, silent)
                    # until the user says "Hey Jarvis" or taps wake in the UI.
                    if self._wake_enabled:
                        self._ensure_wake_detector()
                        self._awake = False
                        self.ui.set_state("SLEEPING")
                        self.ui.write_log("SYS: XYRIS I online — sleeping. Say 'Hey Jarvis' to wake me.")
                    else:
                        self._awake = True
                        self.ui.set_state("LISTENING")
                        self.ui.write_log("SYS: XYRIS I online.")

                    if self._dashboard:
                        await self._dashboard.broadcast({"type": "status", "state": "active"})

                    self._reconnect_event.clear()  # ignore requests from before this session
                    tg.create_task(self._watch_reconnect())
                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio())
                    tg.create_task(self._run_system_monitor())
                    tg.create_task(self._run_background_monitor())
                    tg.create_task(self._run_proactive_mode())
                    tg.create_task(self._run_sleep_watch())
                    if self._dashboard:
                        tg.create_task(self._relay_phone_audio())

                    # Morning briefing — fires once per process launch (if enabled).
                    # Skipped in wake-word mode: it comes up asleep, and a briefing
                    # would mean talking while "asleep".
                    if not self._briefing_sent and get_brief_enabled() and self._awake:
                        self._briefing_sent = True
                        tg.create_task(self._send_startup_briefing())

            except KeyboardInterrupt:
                raise
            except SystemExit:
                raise
            except BaseException as e:
                # Catches both Exception and BaseExceptionGroup (Python 3.11+
                # TaskGroup raises BaseExceptionGroup when tasks are cancelled
                # externally, which `except Exception` would miss, letting the
                # exception escape the while-loop and causing asyncio.run() to
                # start shutdown — resulting in "executor after shutdown" errors).
                # Voluntary reconnect (voice change) — not an error. Rebuild the
                # session immediately with no backoff and no scary logs.
                if _is_reconnect_signal(e):
                    print("[JARVIS] Voluntary reconnect requested.")
                    self._prepare_voluntary_reconnect(e)
                    continue

                # A resumption handle the server will not accept — expired, or
                # belonging to a session it has since dropped. Drop it once and
                # let the next attempt start clean (see _is_resumption_rejection).
                if _is_resumption_rejection(e, _resumed_with):
                    print("[JARVIS] 🔗 Resumption handle rejected — starting a fresh session")
                    self.ui.write_log("SYS: Could not restore the conversation — starting fresh.")
                    self._resume_handle = None
                    self._conn_backoff = 0
                    continue

                err_str = str(e)
                print(f"[JARVIS] Error ({type(e).__name__}): {e}")
                traceback.print_exc()

                # Turn-taking / media / thinking knobs rejected by the server
                # (preview API drift) — drop them first, because they are the
                # newest fields and the cheapest to lose. Proactive audio is
                # tried again on the next pass if the error persists.
                if self._tuned_live and (
                    "INVALID_ARGUMENT" in err_str
                    or "Unknown name" in err_str
                    or "unexpected keyword" in err_str
                    or "realtime_input" in err_str.lower()
                    or "media_resolution" in err_str.lower()
                    or "thinking" in err_str.lower()
                ):
                    self._tuned_live = False
                    print("[JARVIS] Live tuning rejected — reconnecting without it.")
                    continue

                # Proactive audio rejected by the server (preview API drift) —
                # drop it and reconnect with the plain config.
                if self._enhanced_live and (
                    "INVALID_ARGUMENT" in err_str
                    or "proactiv" in err_str.lower()
                    or "Unknown name" in err_str
                    or "unexpected keyword" in err_str
                ):
                    self._enhanced_live = False
                    self.ui.write_log(
                        "SYS: Proactive audio unavailable — reconnecting without it."
                    )
                    continue

                # Invalid API key — stop hammering the API, prompt re-configuration
                if "API key not valid" in err_str or "1007" in err_str:
                    self.ui.write_log("ERR: API key invalid — please re-enter your key.")
                    self.ui.set_state("SLEEPING")
                    self.ui.prompt_reconfig()
                    while not self.ui._win._ready:
                        await asyncio.sleep(1)
                    print("[JARVIS] New API key saved — reconnecting...")
                    _conn_backoff = 3
                    continue

                # Network / timeout errors — log clearly and back off
                is_net_err = any(k in err_str for k in (
                    "TimeoutError", "timed out", "getaddrinfo", "CancelledError",
                    "ConnectionRefusedError", "OSError", "Cannot connect",
                ))
                if is_net_err:
                    _conn_backoff = min(getattr(self, "_conn_backoff", 3) * 2, 60)
                    self._conn_backoff = _conn_backoff
                    self.ui.write_log(
                        f"NET: Connection failed — retrying in {_conn_backoff}s. "
                        "(a VPN may be required)"
                    )
                else:
                    self._conn_backoff = 3
            finally:
                self.session = None
                # Only save if there was a real conversation (≥3 turns)
                if len(self._session_log) >= 3:
                    asyncio.create_task(self._save_session_summary())

            self.set_speaking(False)
            self.ui.set_state("SLEEPING")

            if self._dashboard:
                await self._dashboard.broadcast({"type": "status", "state": "sleeping"})

            # M14: a failed attempt reached the bottom of the loop (real
            # failure or a session that ended) — three in a row engages the
            # offline voice bridge (§3.6). `continue` paths above are
            # deliberate reconnects, not failures, and skip this.
            self.note_connection_failure()

            delay = getattr(self, "_conn_backoff", 3)
            print(f"[JARVIS] Reconnecting in {delay}s...")
            await asyncio.sleep(delay)

def _configure_logging() -> None:
    """One stdlib logging configuration for the app (brain roadmap Phase 1).

    Modules already call logging.getLogger(__name__); without a root
    handler their records vanish. Idempotent — a host (tests, embedding)
    that configured logging keeps its own setup.
    """
    if logging.getLogger().handlers:
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main():
    _configure_logging()
    ui = JarvisUI("face.png")
    _perf_mark("ui_constructed")

    # Install system tray icon (minimize-to-tray on close)
    def _tray_quit():
        import os as _os
        _os._exit(0)
    ui.start_tray(on_quit=_tray_quit)
    _perf_mark("tray_started")

    def runner():
        ui.wait_for_api_key()
        _perf_mark("api_key_ready")
        jarvis = JarvisLive(ui)
        _perf_mark("jarvis_constructed")
        _perf_report()          # AETHER_PERF=1 → print the startup timeline
        try:
            asyncio.run(jarvis.run())
        except KeyboardInterrupt:
            print("\n🔴 Shutting down...")

    threading.Thread(target=runner, daemon=True).start()
    ui.root.mainloop()

if __name__ == "__main__":
    main()