# Automation Intelligence V0.1 — Phase 0: Repository Audit

Method: five parallel deep audits (core runtime, tools/permissions, memory/events,
UI/voice, integrations/verification), every load-bearing claim re-verified against
source. `file:line` references are exact as of `2e1baad` (+ env fixes).

---

## A. What already exists

### Core runtime
| System | Where | State |
|---|---|---|
| Entry | `main.py` — `JarvisLive` (:718) + `JarvisUI` (`ui.py:6320`); asyncio loop on daemon thread (`main.py:3222-3234`) | live |
| AI core | Gemini Live session (tool calls arrive `main.py:2250`); one-shot ladder `core/gemini.py:319 call()` / `:379 text()` / `:389 as_json()` | live |
| Dispatch | `_execute_tool` `main.py:1506` → gate → `_dispatch_tool` `main.py:1634` | live |
| Verifier | `core/verifier.py` `Verifier.verify()` :133; per-action `verify` fns registered `main.py:616`; verdict wording `verification_note` `main.py:597` | live, 3 actions have probes |
| Confirm | `core/confirm.py` `bind/request/resolve` (single slot, 90 s) → `ConfirmBanner` `ui.py:2390` | live |
| Permissions | `core/permissions.py` `PermissionLedger` :43; vocab 8 names :29; gate order forbidden→permission→confirm `main.py:1519/1536/1551` | live |
| Event bus | `core/event_bus.py` — 22 event types (18 never emitted), `Event.correlation_id` free; `emit_event` `main.py:2739` (asyncio-thread only) → `_bridge_event` → ActivityFeed + dashboard | live |
| Activity | `core/activity.py` `ActivityFeed` + `ui.ActivityOverlay` (Phase 32) | live |
| Planner/TaskGraph/Replanner | `core/planner.py` :97, `core/task_graph.py` (Task/TaskGraph, `validate()` cycle check :280, to/from_dict), `core/replanner.py` :152 | **dormant — unit-tested, zero runtime importers** |
| Executor | — | **does not exist** (nothing ever runs a TaskGraph) |
| Scheduler | in-app: only ad-hoc `while True: asyncio.sleep` loops in session TaskGroup (`main.py:3084-3094`); OS-level: `schtasks` one-shots (`actions/reminder.py:146`) | no generic scheduler |
| Memory | `memory/memory_manager.py` (locked whole-file JSON), sessions one-file-per-run `:557` (the only per-record history pattern); `core/memory_v2.py` Trajectory/Procedure — dormant, tested | live + dormant |
| UI | no tabs — floating `_HudOverlay` panels (`ui.py:2368`) + `_centre_overlay` `:6080`; drawer buttons `:4785`; 17 Qt signals for core→UI; `bind_*` seams; state vocab `core/app_state.py` (PLANNING/EXECUTING/VERIFYING/WAITING_CONFIRMATION/RECOVERING all exist) | live |
| Voice | no local intent router — everything goes to the model; capabilities enter via tool declarations; pre-LLM regex intercept exists for instant commands (`_detect_persona_switch` `main.py:1102`); language rules live in `core/prompt.txt:101-147` (Tanglish/Tamil/Telugu, "extract tool parameters in English") | live |
| Dashboard | FastAPI `dashboard/server.py` — route pattern `_build_app` :582, `_auth` :585, `broadcast` :543 | live |

### Tool surface (the capability inventory)
- **21 actions** (`actions/*.py` TOOL dicts) + **8 inline** (`main.py:345/526`) + **3 plugins** (gmail/calendar/quiz).
- `ToolPolicy` = `risk | timeout | requires_confirmation | supports_undo | verification | permissions` (`core/action_loader.py:81`), resolved by `_tool_policy` (`main.py:1602`, order: action → inline → safe defaults → fail-closed).
- Risk today: 8 safe / 12 caution / 1 dangerous (`send_message`); 0 forbidden. Confirmation: `send_message`, `shutdown_jarvis` (+ handler-internal gates).
- **Results are plain `str`** — `ActionRegistry.run()` swallows handler exceptions (`action_loader.py:236`); dispatcher returns `FunctionResponse({"result": str})`. No structured envelope in the live path. (Dormant contracts: `ToolRegistry.execute` → `{success, output, errors}`; `TaskResult(success, output, errors, duration_ms, verified)`.)
- Verification declared: 3 `verifier` (open_app, browser_control, computer_control), 1 `self_check`, 17 `none`.

---

## B. What we reuse (no second registry — ever)

1. **Tool execution** — `ActionRegistry.run` / `PluginRegistry.run` (pure sync, thread-callable) + `ToolPolicy` metadata + timeout clamp.
2. **Gate order** — mirror `_execute_tool`: forbidden → permissions (`PermissionLedger.is_granted`) → confirmation (`core/confirm`) → execute → verify → event.
3. **Verifier** — register workflow probes as `VerificationStrategy.CUSTOM`; adopt `verification_note` wording contract.
4. **Event bus** — emit automation events with `correlation_id = run_id`; the 18 unused types include `VERIFICATION_SUCCESS/FAILURE`, `SYSTEM_ERROR`.
5. **ActivityFeed/dashboard** — free observability via existing `_bridge_event`.
6. **TaskGraph data model** — serialisable nodes/deps/retries/risk; `validate()` for cycles. (Reuse as a *model concept*; our workflow schema supersedes it with edges/triggers/statuses, but keep dict-compat philosophy.)
7. **Confirm gate** — APPROVAL nodes and activation prompts ride the existing single-slot banner.
8. **gemini.as_json (SMART)** — bounded LLM steps: goal parsing, workflow generation, AI nodes.
9. **Session-per-file storage pattern** (`memory/sessions/`) — template for run history.
10. **Panel pattern** — `_HudOverlay` + drawer button + `bind_*` seam + test shape (`tests/test_activity_center.py`).
11. **Persona-switch intercept pattern** — pre-LLM regex for instant local commands if needed.
12. **core/undo** — compensating actions for reversible tool failures.

## C. What needs extension (minimal seams)

| Extension | Seam (existing pattern) |
|---|---|
| New model-facing control surface | `actions/automation.py` TOOL dict — auto-discovered, gets gate/permissions/verification for free |
| Automation engine construction | one block in `JarvisLive.__init__` with **injected** resolvers (policy, runner, emit, confirm, verifier, ui log) — engine never imports `main` |
| Scheduler task | one `tg.create_task(self._run_automation_tick())` in `run()` beside existing monitors |
| Event kinds → HUD | add labels in `core/activity.py:_KIND_LABELS` |
| UI panel | `AutomationOverlay(_HudOverlay)` + drawer button + `bind_automation_store` (exact ActivityOverlay copy) |
| Prompt capability lines | `core/prompt.txt` gets automation verbs (declarations already auto-described via `_describe_tools`) |

## D. What is missing (must be built)

1. **Workflow data model** — versioned Automation/Node/Edge/statuses (schema section 3 of spec).
2. **Workflow validator** — connectivity, cycles, params, unavailable tools, permission conflicts, unreachable branches, loop limits (section 16).
3. **Automation runtime** — sequential/branch/loop/retry/timeout/cancel/pause/resume/approval-wait/state-persist/history (section 18). *No executor exists anywhere today.*
4. **NL generator** — intent → goal model → capability discovery → graph (sections 13-15).
5. **Risk/permission engine for workflows** — node risk inherited from `ToolPolicy`, aggregate per workflow, high-risk → pause (section 12).
6. **Trigger engine** — manual + schedule + file-watch ticker (section 5). No in-app scheduler exists.
7. **Persistence + versioning** — `config/automations/*.json` + immutable version history + one-file-per-run history (sections 22-23).
8. **Observation/verification layer per node** — execute → observe → verify (section 19).
9. **Recovery** — retry policy, safe replan within boundaries, ask-user (section 20) — `Replanner` gives failure classification to build on.
10. **Idempotency keys** (section 21).
11. **UI**: automation canvas/preview/activity panels (sections 24-27).
12. **Voice tool + prompt** for automation verbs (section 28).
13. **Templates** (section 30) built from real capabilities only.
14. **Structured node result envelope** — adapter around `str` results: `{success, status, message, data, verification, reversible, error_code, execution_time, metadata}` (section 17).

## E. What must NOT be changed

- `_receive_audio` tool-batch contract (`main.py:2250-2259`); audio/echo pipeline; reconnect ladder; vision state machine; `core/gemini.py` ladder + model slots.
- `ui.py` internals — touch only through facade methods/signals/`bind_*`.
- `action_loader` validation rules, `INLINE_TOOL_META` (automation tools go in `actions/`), confirm single-slot semantics, memory `_lock`.
- Existing tests stay green (1258 baseline).

## Key risks found (drive design decisions)

1. **`ActionRegistry.run` swallows exceptions** → workflow nodes cannot trust `str` results alone. *Decision: node success = (no timeout) ∧ (no failure-string) ∧ (verification ≠ FAILED); `verification="verifier"` nodes get real probes; workflow records `verified` separately. Never claim success without it.*
2. **Plugins have no policy** (gmail/calendar ungated) → automation must apply its *own* workflow-level permission/risk metadata for plugin nodes; risk analyzer treats gmail send/delete as HIGH regardless of declared policy.
3. **`emit_event` is asyncio-thread-only** → runtime executes runs as asyncio tasks on the main loop (approvals use `confirm.request` + `asyncio.Future`).
4. **Confirm gate is single-slot, 90 s** → one approval at a time; approval waits queue (APPROVAL node pauses run, banner shown when foregrounded).
5. **No cross-process file locks** → store uses write-temp + `os.replace` (atomic; new pattern for this repo, mandated by crash-safety need).
6. **Performance** — generation is one bounded `gemini.as_json` call; capability catalog cached; store indexed by id; runs incremental; no full-state loads; UI polls like ActivityOverlay (700 ms), never blocks main thread.

## Architecture decision (the seams)

```
core/automation/
  schema.py      Workflow/Node/Edge/RunState dataclasses, statuses, to/from_dict, versioning
  capability.py  live catalog from ActionRegistry+PluginRegistry+INLINE (injected), policy resolver
  validate.py    graph/schema/param/risk/trigger validation → VALID|INVALID|REQUIRES_*
  risk.py        node risk/permissions inherited from ToolPolicy (+ plugin rules), aggregate
  runtime.py     asyncio executor: sequence/branch/loop/retry/timeout/cancel/pause/approval/observe/verify/recover
  generator.py   intent parser → goal model → graph generator (gemini.as_json, bounded, clarification on ambiguity)
  store.py       config/automations/*.json (atomic), versions, runs/ one-file-per-run, stats
  triggers.py    manual/schedule/file-watch tick loop (asyncio, survives via main TaskGroup)
actions/automation.py   model-facing tool: create/list/show/run/test/pause/resume/stop/delete/explain
ui.py: AutomationOverlay + AutomationCanvas preview + drawer button + bind_automation_store
main.py seams: __init__ engine build (injected resolvers) · run() tick task + bind · emit bridge (unchanged)
```

Engine never imports `main`/`ui`; all dependencies injected → testable with `__new__` fakes.

## Phase → git checkpoint map (spec §42)

`automation-v0-audit` (this report) → `-schema` (Phases 1-4) → `-runtime` (Phase 5 + 8) →
`-generator` (Phase 6) → `-security` (Phase 7 + 13) → `-store` (Phase 9) →
`-ui` (Phase 10 + 12) → `-voice` (Phase 11) → `-release` (Phase 14 regression).

*As delivered:* phases were consolidated into fewer commits — `-runtime` and
`-store` both point at `6e5de37` (Phases 5+8+9), `-ui` and `-voice` both at
`e7bec45` (Phases 10+11+12). Every phase in this map is reachable from its tag.
