# DASHBOARD V3 — Intelligence Command Center (design + audit)

Status: Phase 0 audit + Phase 1 design (2026-10-15). Phases 2–10 update this
file as they land. Honesty rules from the master spec apply verbatim: no
fake telemetry/agents/memory/provider status, no second state system, no
second workflow runtime, no duplicated timers/subscriptions, no credential
exposure, permissions unchanged.

Note: no reference image was shipped with the spec — the visual direction is
taken from spec §2 (textual). Every panel below names the **single real
data source it may read**; if a source does not exist, the panel is not
built (not faked).

---

## Phase 0 — audit (what exists, file:line)

**Framework / entry:** PyQt6 desktop app; `main.py` (`JarvisLive`) wires
callbacks into `ui.JarvisUI` facade → `MainWindow` (`ui.py:4902–7518`,
7,873-line single-file UI; entry `main.py`). Window 980×700 default,
min 820×580 (`ui.py:86–89`).

**MainWindow tree today:** header 54 px (`_build_header` 5751) · body =
left panel 148 px sys-monitor (`_build_left_panel` 5828) + center vertical
`QSplitter` (`_center_split` 5030; page 0 `HudCanvas` in `_hud_cam_stack`
5026, content/quiz splitter panels hidden by default) + right panel 340 px
(`_build_right_panel` 5899: activity `LogWidget`, file drop, command input,
interrupt, mic mute) · footer 22 px. Navigation today = ⚙ quick drawer
(5968) → centered floating overlays (`_HudOverlay` base 2950): Memory
(3181), History (3377), Activity (3580), Automations (4031 + graph canvas
3750), Plugins, Persona, Audio, Remote, Customize. One `QStackedWidget`
(HUD↔camera). **No sidebar nav, no tabs.**

**Living Neural Core:** `HudCanvas` (`ui.py:403–1544`) — reactor/face
styles, V2 ring set + automation band + parallax (V2 project, committed).
State render seam: `MainWindow._apply_state` (7463) ← `_state_sig` ←
`JarvisUI.set_state` (7724). Single state system `core/app_state`
(VALID_STATES: IDLE, LISTENING, UNDERSTANDING, THINKING, PLANNING,
EXECUTING, OBSERVING, VERIFYING, SPEAKING, WAITING_CONFIRMATION, ERROR,
RECOVERING, SLEEPING + MUTED, INITIALISING).

**Backend seams (reuse, do not duplicate):**
- Event bus `core/event_bus.py` (publish/subscribe; wildcard bridge
  `main.py:2891` → `core/activity.ActivityFeed` (200-row ring) + dashboard).
  Live-published kinds: `TOOL_RESULT`/`TOOL_ERROR` (with verification
  verdict dict), `CUSTOM` (automation run lifecycle, degraded start/end),
  `USER_INTERRUPT`, `SYSTEM_STARTUP`. Most EventType members are
  defined-but-unpublished — the dashboard must render only what flows.
- Activity: `ActivityFeed` labels in `core/activity.py:27–70`; consumed by
  `ActivityOverlay` (700 ms drain, started on show only).
- Telemetry: `ui._SysMetrics` (249) daemon sampler — CPU/RAM/NET 2 s,
  GPU (pynvml/NVML) + temp (psutil/WMI) every 6 s; `-1.0` = unavailable;
  `snapshot()` 390. Uptime (`psutil.boot_time`) + proc count in
  `_update_metrics` (5700). `actions/system_monitor.get_system_status()`
  exists for the tool layer (no subprocess).
- Memory: `memory/memory_manager.get_memory_stats()` (622: total,
  by_category, oldest/newest, size_bytes, sessions),
  `all_entries_for_ui()` (396), `get_session_count()` (616),
  `is_sensitive_key_value` (417) — existing `MemoryOverlay` is the
  inspector (per-row `forget`).
- Model/provider: `core/model_manager` — `MODEL_MANAGER.snapshot()` (284:
  per-rung attempts/ok/fail/avg_latency/last_latency/cooling/last_error),
  `is_cooling()`, `LIVE_MODEL`; `config_manager.is_configured()` (key
  length gate) — **status from local state only, never a paid probe**.
- Automation: `config/automations/` store (list/list_runs/all_runs),
  `engine.status_summary()`, runtime active runs;
  `bind_automation_store`/`bind_automation_runner` seams already wired
  (`main.py:3151`); `AutomationOverlay` + `AutomationCanvas` is the
  existing center; run events also drive the HUD band.
- Voice: mic mute (`_toggle_mute` 7425), interrupt (7421 → `on_interrupt`),
  wake/PTT, persona (header indicator 5801), voices
  (`AVAILABLE_VOICES`), live audio level (`set_audio_level`) and visemes —
  all real state; states flow through `set_state`.
- Brain V0.1 observable stages = the state machine strings above
  (UNDERSTANDING/PLANNING/EXECUTING/VERIFYING/RECOVERING...). There is no
  planner file, no capability-selection event: **stages may be shown only
  from `app_state` transitions/events — never simulated**.
- Agents: **one assistant** (the `JarvisLive` loop + `dev_agent` tool).
  Tasks that really exist: automation runs (runtime/store). No agent
  registry, no task list → the Agent & Task panel must say exactly that.
- Notification path: `ConfirmBanner` (WAITING_CONFIRMATION) + OS/tray
  toasts; there is no notification center — the bell may only reflect
  pending confirmation / open the real activity feed.

**Branding (user-facing, replace):** `APP_VERSION = "MARK LIV"` (83),
window title 4937/7227, header badge 5764, tray tooltip/menu
(`tray_manager.py:76,82`), plus JARVIS strings at ui.py
1803, 2207, 3125/3128, 3276, 4231, 4269, 4865, 5195, 5791, 6948 and
`main.py:3236/3240` boot lines (they land in the on-screen log).
Keep: internal autostart/shortcut identifiers (`JARVIS_AI`, `jarvis.ico`),
module headers, LICENSE, footer attribution ("By FatihMakes"), and the
model's persona system prompts (product behavior, not UI chrome).

**Styling:** palette class `C` (94–115): BG `#00060a`, PANEL `#010d14`,
BORDER `#0d3347`, PRI `#00d4ff`, GREEN `#00ff88`, ACC `#ff6b00`,
ACC2 `#ffcc00`, RED `#ff3355`, TEXT `#8ffcff`… live hue re-theme
(`apply_ui_accent`/`retheme_all_widgets` 119–198) — inline per-widget QSS
+ QPainter for painted widgets; Courier New throughout.

**Timers (do not duplicate):** clock 1 s (5060), sys-metrics 2 s (5066),
HUD 16 ms w/ paint throttle (490), activity drain 700 ms (overlay, show-
gated), automation poll 2.5 s (overlay), `_SysMetrics` thread 2 s.

---

## Phase 1 — target layout

```
┌────────────────────────────────────────────────────────────────────┐
│ A TOP BAR 54px: [AETHER-I · protocol] [STATE chip] [HEALTH cpu/mem] │
│                 [notices●] [persona] [clock/date] [⚙]              │
├──────────┬────────────────────────────────────────┬────────────────┤
│ B NAV    │ C LIVING NEURAL CORE                   │ D ACTIVITY     │
│ rail     │   (HudCanvas — preserved identity,     │ (ActivityFeed, │
│ 176px    │    V2 rings/parallax/automation band)   │  bounded)      │
│ collapses│                                        │ E AGENT+TASKS  │
│ to 48px  │                                        │ (1 assistant + │
│          │                                        │  real runs)    │
│ Command  ├────────────────────────────────────────┤                │
│ Center   │ (content/quiz splitter panels kept)    │ J COMMAND      │
│ System   │                                        │ input·mic·     │
│ Monitor  │                                        │ interrupt      │
│ Memory   │                                        │                │
│ Auto-    │                                        │                │
│ mations  │                                        │                │
│ Conver-  │                                        │                │
│ sations  │                                        │                │
│ Activity │                                        │                │
│ Settings │                                        │                │
├──────────┴────────────────────────────────────────┴────────────────┤
│ FOOTER 22px (shortcuts + attribution)                              │
└────────────────────────────────────────────────────────────────────┘
```

**View routing (single mechanism, no second state system):** a
`QStackedWidget` in the center column. Pages:
- **0 Command Center** — the existing center (HudCanvas stack + content/
  quiz splitter) as-is; the default.
- **1 System Monitor** — the five real metrics (moved from the old left
  panel: same `_SysMetrics` singleton, same 2 s timer), uptime/proc
  counts, plus the **MODEL/PROVIDER STATUS** card (local snapshot only).
- **2 Memory** — `get_memory_stats()` summary + category breakdown +
  session count + "open inspector" → existing `MemoryOverlay` (entries
  stay behind the inspector; counts only in the card).
Nav destinations that exist only as overlays open those **existing**
overlays (preserved, not duplicated): Automations → `AutomationOverlay`,
Conversations → `SessionHistoryOverlay`, Activity → `ActivityOverlay`,
Settings → existing quick drawer. The nav highlight follows
page-or-overlay honestly (overlay open ⇒ its item highlighted).

**A Top bar:** reuses the existing 1 s clock/date timers and persona
indicator; adds: AETHER-I wordmark (replaces "MARK LIV" badge/title),
a **STATE chip** fed by the same `_apply_state` seam (label = real
`app_state` text, glyph+colour from `HudCanvas._state_accent` vocabulary —
color + text, never color-only), a **HEALTH strip** = CPU% and MEM% from
`_SysMetrics.snapshot()` (GPU/TMP render "N/A" when `-1.0` — unavailable ≠
0), a notice dot that lights **only** while `WAITING_CONFIRMATION` is
pending, and the existing ⚙ drawer button. No new timers.

**B Left nav:** new rail (176 px, collapses to 48 px icon-only below
900 px width or on demand). Items map 1:1 to real capabilities listed
above; unimplemented spec sections (Knowledge, Tools & Skills, Agents as a
section) are **not** shown — Tools/Plugins stay reachable via Settings
drawer as today.

**D Activity panel:** the structured `ActivityFeed` (the *user-facing*
event stream — not the raw `LogWidget` debug text; `LogWidget` remains for
command echo but the panel shows feed rows with timestamp/type/glyph).
Phase 4 reads `core/activity.py` and picks a **snapshot/pull API so the
overlay and the panel do not eat each other's rows** (if the feed pops,
extend it with a cursor/offset readers API rather than duplicating the
ring).

**E Agent & Task panel:** header line states the truth — "1 assistant
(single runtime)". Rows: assistant row (state from `app_state`), then
**active automation runs** (runtime/store, poll co-scheduled with the
existing 2.5 s overlay cadence, visible-only), then last 3 finished runs.
Statuses from `RunStatus` only. If no runs exist: explicit empty state
("No active tasks") — never placeholder agents.

**J Voice/command:** the existing input row + interrupt + mic-mute moved
to the bottom of the right rail (fixed, always reachable regardless of
panel collapse), with state text bound to real mic/speaking flags
(existing `_style_mute_btn` / `set_audio_level` seams).

**I Automation Center:** existing `AutomationOverlay`/`AutomationCanvas`
+ store/runner bindings (Phase 7 enriches inspect/verification/history
views inside that overlay). No second runtime, activation still only via
explicit user/tool action.

### Design tokens (spec §15)

| Token | Value (source) |
|---|---|
| `bg.base` | `#00060a` (`C.BG`, deep navy-black) |
| `bg.panel` | `#010d14` / `#010f18` (`C.PANEL/PANEL2`) — glass = these at 92 % alpha where stacked |
| `border.tech` | `#0d3347`, hover `#1a5c7a` (`C.BORDER/BORDER_B`) |
| `accent.primary` | `#00d4ff` electric cyan (`C.PRI`) |
| `status.ok` | `#00ff88` emerald (`C.GREEN`) |
| `status.busy` | `#ffcc00` (`C.ACC2`) / `#ff6b00` (`C.ACC` speaking) |
| `status.err` | `#ff3355` (`C.RED`) · muted `#ff3366` |
| `text.1/2/3` | `#d8f8ff` / `#8ffcff` / `#5ab8cc` (`C.WHITE/TEXT/TEXT_MED`), dim `#3a8a9a` |
| typography | Courier New (5–17 pt), letter-spacing on wordmarks |
| radius | 6 px cards / 4 px chips (new panels; overlays keep their look) |
| spacing | 8 px grid (8/12/16/24) |
| glow | single restrained shadow `0 0 12px rgba(0,212,255,.18)` on focus/active only |
| motion | 160 ms hover, 240 ms view/panel transitions; `reduced-motion` → 0 ms + no pulse |
| breakpoints | ≥1100 full · 900–1100 right rail 300 px · <900 nav collapses to 48 px, right rail toggled by header button, min window 820×580 unchanged |

Re-theme safety: all new tokens derive from `C.*` so the existing hue
re-theme (`retheme_all_widgets`) keeps working — no hard-coded hex in new
code except documented token aliases.

### Panel → source matrix (the contract)

| § | Panel | Source (only) | Availability handling |
|---|---|---|---|
| A | state chip | `app_state.current()` via `_apply_state` | never blank: INITIALISING fallback |
| A | health | `_SysMetrics.snapshot()` | `-1.0` → "N/A" |
| A | notices | `WAITING_CONFIRMATION` pending flag | dot only when true |
| C | core | `HudCanvas` (unchanged identity) | sticky fallback orb (V2) |
| D | activity | `core.activity.ActivityFeed` | empty state "No activity yet" |
| E | tasks | automation store/runtime runs | empty state "No active tasks" |
| E | agents | known single runtime | static truth line, no invented agents |
| F | metrics | `_SysMetrics` + `psutil.boot_time/pids` | N/A vs 0 distinguished |
| G | memory | `get_memory_stats()` | counts only; inspector = existing overlay |
| H | providers | `MODEL_MANAGER.snapshot()`, `is_configured()`, `LIVE_MODEL` | "Not configured"/"Cooling"/"Error" from real fields; no network probe |
| I | automations | existing overlay + store/engine | existing permission/confirm gates |
| J | voice | existing mute/interrupt/state seams | mic text reflects actual recording state |

### Phase map (spec §19 → this repo)

0 audit (this file) · 1 design (this file) · 2 shell: top bar + nav rail +
center stack + branding sweep · 3 core: integrate/verify HudCanvas in new
shell (no identity change) · 4 activity + agent/task panels · 5 telemetry
page + memory page · 6 provider card · 7 automation center enrichment ·
8 voice/command polish · 9 responsive + measured perf + a11y ·
10 regression + DoD. Git checkpoint per phase.

## DoD checklist (spec §21 — filled in Phase 10)

- [ ] AETHER-I branding consistent
- [ ] Existing neural core preserved
- [ ] Coherent visual identity (tokens above)
- [ ] Panels readable + responsive
- [ ] Activity logs do not overlap
- [ ] Telemetry from real sources
- [ ] Memory statistics accurate
- [ ] Provider status truthful
- [ ] Voice controls reflect actual state
- [ ] Automation controls use existing engine
- [ ] Brain visualization uses real observable events
- [ ] Permissions remain enforced
- [ ] Performance measured (real numbers, env timer)
- [ ] Accessibility addressed (contrast, no color-only, reduced-motion)
- [ ] Existing features functional (suite green)
- [ ] Relevant tests pass
- [ ] Known limitations documented

## Known limitations (living)

- No reference image shipped with the spec; direction from §2 text only.
- Dashboard shows observable state only — no model reasoning, no simulated
  brain stages, no fabricated agents.
- Model status is local-config + local-run health; it never probes the API
  (cost/latency) so "connected" is claimed only when a live session flag
  exists.

## Phase progress (living)

- Phase 0–1 (b77b1a0): audit + this design.
- Phase 2 (6bee183): shell — nav rail, center stack (page 0 = existing
  HudCanvas untouched, page 1 = System Monitor), top-bar state chip +
  health strip, AETHER-I branding sweep, responsive collapse.
- Phase 3–4 (this checkpoint): core verified in page 0 (chip ↔ HUD single
  sink via `_apply_state`, `_auto_sig` band seam intact); right rail now
  tabbed ACTIVITY (`_FeedPanel`, own-cursor `drain_since` on the shared
  ring, visible-only 1.5 s timer, bounded 60 rows) + LOG (existing
  `LogWidget`, preserved); `AGENTS & TASKS` (`_TasksPanel`, single-runtime
  truth + real `AutomationStore.all_runs`, empty state, no fake rows).
  Nav settings toggle bug fixed (`_toggle_drawer` needed its `checked`
  arg). Coverage: `tests/test_dashboard_v3.py` (25 tests).
- Remaining: Phase 5 telemetry + memory page · Phase 6 provider card ·
  Phase 7 automation center · Phase 8 voice polish · Phase 9 perf/a11y ·
  Phase 10 regression + DoD.
