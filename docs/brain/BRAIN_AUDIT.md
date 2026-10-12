# AETHER-I Brain V0.1 — Phase 0: Repository Audit

**Scope:** `C:\Users\Toshiba\Downloads\Mark-LIV-main` at commit `db7689c`.
**Method:** code-only verification (grep/read across `main.py`, `ui.py`, `core/`, `actions/`, `plugins/`, `memory/`, `dashboard/`, `config/`). Documentation and roadmaps were never trusted as evidence.
**Rule honored:** audit and design only — no application source was modified.

## Evidence legend

| Tag | Meaning |
|---|---|
| **VERIFIED** | Claim read directly in code, cited `file:line` |
| **INFERRED** | Reasoned from verified facts, not directly cited |
| **PROPOSED** | Design recommendation, not present in code |
| **UNKNOWN** | Could not be determined from the code |

---

## 1. Component inventory (spec §1)

| # | Component | Source (VERIFIED) | Status | Key note |
|---|---|---|---|---|
| 1 | Application entry points | `main.py:3312-3338` (`main()`), `JarvisLive` `main.py:718`; `ui.py:6935` `JarvisUI`, `ui.py:4320` `MainWindow`; `run.bat:3` (hardcodes `D:\MarkLIV-venv` — unportable); dashboard embedded only (`main.py:3062-3073`) | IMPLEMENTED | `JarvisLive` is a god-object orchestrator (3,338 lines) |
| 2 | AI model integration | Live session `main.py:3139-3146`; one-shot funnel `core/gemini.py:319` `call()`; ladder `core/model_manager.py:58-62,117-138` | IMPLEMENTED (Gemini only) | No Anthropic/Groq/OpenAI references outside dormant `core/llm_client.py` |
| 3 | Model/provider config | `config/api_keys.json` (plaintext), readers ×6: `main.py:307`, `core/gemini.py:152`, `memory/config_manager.py:46`, `dashboard/server.py:62`, +2 leftovers | PARTIAL | No provider switching for the conversation; ladder only demotes quota-cooled rungs (`model_manager.py:169-201`) |
| 4 | Conversation management | Server-side (Live resumption + sliding window `main.py:1463-1470`); client `_session_log` `main.py:795`; summaries `memory_manager.py:500-523` (cap 3) | IMPLEMENTED / shallow persistence | `_session_log` unbounded in-process; summary failure after log reset loses nothing on disk (raw transcript saved first, `main.py:2591`) — INFERRED loss window small |
| 5 | Intent understanding | **No global module.** Prompt-driven tool-calling (`core/prompt.txt:28-32`); keyword persona switch `main.py:1143-1176`; per-feature classifiers `actions/code_helper.py:121`, `core/automation/generator.py:233` | MISSING as a module; routing implemented via model | See gap analysis §A |
| 6 | Context management | `_build_config` `main.py:1379-1492` (time/identity/memory/persona/prompt per connection); injections: vision `2108-2149`, proactive `2668-2705`, monitors `2613-2659`, briefing `2443-2517` | IMPLEMENTED | Memory written mid-session is **not** re-injected until reconnect (acknowledged `main.py:468-476`) |
| 7 | Planning & reasoning | Prompt instruction only (`core/prompt.txt:130-136`); dormant cluster: `core/planner.py:62`, `core/reasoning.py:82`, `core/replanner.py:63`, `core/self_checker.py:70` — tests-only | MISSING at runtime | See §5 below; dormant code carries defects |
| 8 | Task graph | `core/task_graph.py:157` (cycle check `:280`) — **no executor, tests-only**; live graph = `core/automation/schema.py:324` | DORMANT (v1) / IMPLEMENTED (automation) | See §6 |
| 9 | Tool registry | **Live:** `core/action_loader.py:308` `ActionRegistry` + `core/plugin_loader.py:221` `PluginRegistry` + inline `main.py:345-517`; assembly `main.py:1427-1429`. **Dormant:** `core/tool_registry.py:197` | IMPLEMENTED (3-source live design) | Four risk vocabularies coexist (see §8) |
| 10 | Tool execution | `_execute_tool` `main.py:1547` (gate order: forbidden `1560` → permissions `1570` → confirm `1592` → dispatch `1641`); `_dispatch_tool` `main.py:1675` (timeouts, events, verification) | IMPLEMENTED | Single dispatch spine shared with automation (`adapters.py:174`) |
| 11 | Permission management | `core/permissions.py:43` `PermissionLedger`, 8-word vocab `:29-38`, fail-closed on corrupt file `:88`, missing file = all granted `:65-66`; evaluated `main.py:1577` | IMPLEMENTED | `config/permissions.json` absent in this checkout → everything granted by default |
| 12 | Confirmation handling | `core/confirm.py:46` 90 s timeout; `request:82`, `resolve:115`; only `ui.py:6766-6773` may resolve (test-enforced `tests/test_tool_metadata.py:263-279`) | IMPLEMENTED — strongest-designed component | Single global slot; worker exceptions only logged (`confirm.py:147-148`) |
| 13 | Verification | `core/verifier.py` strategies `:29-39`; wiring `main.py:593,616,1854,1886`; **only `CUSTOM` runs in production**, 3 of ~33 tools verify; `SCREENSHOT`/`DOM_CHECK` explicit stubs `:350,:359`; `"self_check"` is a dispatch no-op (`main.py:1854`) | PARTIAL | Verdict is a **text note appended to the result** (`main.py:597-613`) — an instruction to the model, not an enforcement |
| 14 | Error recovery | No dispatcher retries by design (`core/prompt.txt:131`); real retries: automation nodes `runtime.py:410-438`, connection backoff `main.py:3277-3289`, model ladder, image gen, `core/undo.py`; dormant `core/replanner.py` | PARTIAL | Recovery decisions delegated to the model via failure strings |
| 15 | Memory & retrieval | `memory/memory_manager.py` (see §4) | PARTIAL | Lexical only; `core/memory_v2.py` dormant |
| 16 | Vision & perception | `core/visual/provider.py`, live provider `pollinations.py`; `gemini_imagen.py:51` honest stub; capture `actions/screen_processor.py` | IMPLEMENTED / stub provider | |
| 17 | Voice processing | STT `core/stt.py` (Whisper/Vosk), TTS `core/tts.py` (Edge/Kokoro/ElevenLabs), echo `core/echo.py`, degraded offline `core/degraded.py` | IMPLEMENTED | |
| 18 | Automation Intelligence | `core/automation/` (12 files) + `actions/automation.py` + wiring `main.py:846-879,3081-3120` | IMPLEMENTED | See §6 — integration clean, three live leaks |
| 19 | Workflow generation | `core/automation/generator.py:355` `generate()` (intent → clarify → draft → validate → 1 repair), templates `templates.py:195` | IMPLEMENTED | |
| 20 | Workflow runtime | `core/automation/runtime.py:197` `execute()` — sequence/branch/loop/retry/pause/approval/dry-run/idempotency | IMPLEMENTED | Own assertion engine `:883` (not `core/verifier.py`) |
| 21 | Scheduling & events | Automation ticker `engine.py:233` (5 s poll, started `main.py:3117`); triggers manual/schedule/`file_watch` (polling snapshot diff, `triggers.py:135-156`); app loops `main.py:3185-3195`; event bus `core/event_bus.py:28` with single bridge `main.py:2830` | IMPLEMENTED | `file_watch` = polling, not an OS watcher (VERIFIED) |
| 22 | Plugin architecture | `core/plugin_loader.py:221`, `PLUGIN` dict contract, enable/config `memory/config_manager.py:326-384` | IMPLEMENTED, **no sandbox** | In-process, full interpreter; policy defaults to safe (`main.py:1653-1654`) |
| 23 | Logging & observability | UI `LogWidget` `ui.py:1025`; event bus (sparse emissions); activity ring `core/activity.py:24` | PARTIAL | **No `logging` configuration anywhere** — 20+ modules call `getLogger` with no handler; `app.err/out.log` produced externally, not by code |
| 24 | Persistence | `memory/long_term.json` (non-atomic), `memory/sessions/*.json`, `config/api_keys.json` (8+ non-atomic writers), `config/automations/**` (**atomic** `store.py:57-65` — the only one), no SQLite | PARTIAL | |
| 25 | UI & AI Core states | `core/app_state.py:38-54` 13 states + 2 system; single sink `ui.py:6877`; pyqtSignal facade `ui.py:4321-4335` | IMPLEMENTED | `PLANNING`/`OBSERVING` have HUD paint but **no emitter** (`app_state.py:12-14`, VERIFIED) |
| 26 | Configuration & security | `memory/config_manager.py` (490 lines, typed accessors); see §8 | PARTIAL | Plaintext key, dashboard `0.0.0.0`, `exec()`/`shell=True` surfaces |

---

## 4. Memory audit (spec §4)

| Capability | Status | Evidence (VERIFIED) |
|---|---|---|
| Working memory | PARTIAL | `_session_log` `main.py:795` (turn strings, unbounded in-process); `session_memory` context key always `None` (`main.py:1812,1833`) |
| Episodic memory | PARTIAL | `memory/sessions/*.json` full transcripts `memory_manager.py:557-579`; summaries capped at **3** (`_SESSION_MAX:497`), consumed by briefing (`pop_last_session:526`) |
| Semantic memory | IMPLEMENTED (basic) | `memory/long_term.json` categorized entries, `remember/search_memory` `:413/:349`, 200 KB trim guard `:40,:96-116` |
| Procedural memory | DORMANT | `core/memory_v2.py` `ProceduralMemory:336` tests-only; in practice automation workflows are the live procedural store |
| Retrieval | IMPLEMENTED (lexical) | `search_memory` + `_score` `:326-346`: exact-key +10 / substring +6/+3 / category +1. **No embeddings/vector store anywhere** (VERIFIED by scan) |
| Relevance ranking | PARTIAL | Heuristic lexical score only; `MemoryRetriever` in dormant `memory_v2.py:427` |
| Persistence | PARTIAL | `long_term.json` direct `write_text` — **not atomic** (`:124-127`); automation store is atomic |
| Context selection | IMPLEMENTED | `format_memory_for_prompt` `:194-321`: identity full, recent ≤900 chars, index of rest → `recall_memory` |
| Memory updates | IMPLEMENTED | `remember` overwrites by key; tool path `main.py:1697-1709` |
| Duplicate prevention | UNKNOWN | No dedup logic found in `memory_manager.py` (searched: merge/dupe/exists) |
| Retention & deletion | IMPLEMENTED | `forget` + `memory_controller.py` (confirm-gated `clear`), 200 KB trim with notifier |
| Sensitive-data protection | PARTIAL | `is_sensitive_key_value` + confirm gates in `actions/memory_controller.py`; `.gitignore` excludes memory stores; no runtime redaction of secrets written to memory |

**Smallest useful improvements (PROPOSED, ordered):**
1. Atomic writes for `long_term.json`/`sessions` (copy the `store.py:57-65` temp+`replace` pattern — one helper, three call sites).
2. Wire `core/memory_v2` retrieval *behind* `search_memory` only if a labeled relevance test (EVALUATION_PLAN §M1) shows lexical is failing — do not adopt wholesale.
3. Session-summary cap 3 → configurable; dedupe keys on `remember`.
4. Never store values matching the key patterns already scanned by `tests/test_security_confirmation_audit.py:145-148`.

---

## 5. Reasoning & planning audit (spec §5)

**How goals are interpreted today:** entirely by the Gemini Live model via `core/prompt.txt` instructions plus tool schemas. Multi-step work = the model chaining `function_call`s one at a time (`main.py:2291-2300`). The one explicit planner is the automation generator (`generator.py:355`) — LLM → validated graph.

**How unavailable capabilities are detected:** at generation/validation time (`validate.py:155-162` against the live catalog) and at dispatch time (policy/permission gates). For ordinary chat, the model self-limits via `{limits}` in the prompt (`main.py:263-291`).

**How failures are recovered:** failure strings flow back to the model (`main.py:1845-1849`), which decides; automation has structured retry/idempotency (`runtime.py:410-438,482-493`).

**Dormant planner cluster — defects found (VERIFIED, do NOT wire as-is):**
- `core/planner.py:230-334` — single-phrase tasks all get id `"task_0"`; `" and "` splitting creates duplicate ids + self-dependency (`:197-228`); `graph.validate()` failures are only logged (`:131-132`).
- `core/planner.py:373` — risk comparison uses **string ordering** (`"safe" > "caution"` is True) → safe tasks wrongly demand confirmation.
- `core/reasoning.py:132-140` — fixed 5-line template ending *"Task requires further specific logic implementation"*; `_check_contradictions` always `[]` (`:179-180`) → `self_check` can never pass. **Placeholder.**
- `core/replanner.py` keyword failure classifier — tests-only.

**Unified planning interface?** Not yet. The live model-chaining path works and the automation generator already produces explicit graphs. Building a second general planner now would create exactly the competing system the spec forbids (INFERRED from evidence above; see TARGET_ARCHITECTURE §P1).

---

## 6. Automation Intelligence integration (spec §6)

**Boundary verdict: the central promise — one dispatcher, one ledger, one confirm gate, read-only capability catalog — is genuinely implemented (VERIFIED).**

Connected seams (VERIFIED): catalog refresh from live registries `main.py:873-875,3114-3116`; shared dispatch `adapters.py:174 → main.py:1675`; same `PermissionLedger` object `main.py:857,862`; real confirm gate `main.py:854-855`; memory adapters → `memory_manager` `main.py:852-853`; ticker `main.py:3117`; event allowlist + `correlation_id` `main.py:2819-2828`. Automation does **not** use dormant `core/planner.py`/`task_graph.py` — it superseded them with its own schema (no imports, VERIFIED).

**Three live leaks (VERIFIED):**
1. **Verifier honesty gap** — `runtime.py:8-9` promises node success requires verifier agreement, but `looks_like_failure` (`adapters.py:26-33`) does not match the `[VERIFICATION FAILED]` verdict string (`main.py:608-610`) → a verifier-failed step records as success.
2. **Gate mirror drift** — `adapters.py:128-171` re-implements `_execute_tool`'s gate order (necessary: `_dispatch_tool` deliberately doesn't re-gate, `main.py:1676-1679`) but is missing the `shutdown_jarvis` no-user-input fail-closed rule (`main.py:1593-1612`).
3. **`capability.py:84-88` bug** — reads `ActionRegistry._all_records` expecting a dict; it is a **list** (`action_loader.py:180`) → action parameter schemas/availability never reach the catalog → required-param validation (`validate.py:166`) is inert for action tools.

**Also noted:** plugin confirmation is stamped in analysis (`risk.py:108-109`) but never enforced at runtime (dispatcher plugin policy = safe default, `main.py:1653-1654`); concurrent *manual* runs of one workflow unguarded (`wf.status` never set RUNNING, `engine.py:164-165`; only `tick()` checks `active_run_for:222`); dead duplicate failure sniffer `schema.py:189-206`.

**Recommended integration boundary (developed in TARGET_ARCHITECTURE):** Tool Registry (Action+Plugin+inline) remains the sole authority over *what can execute and under what policy*; automation owns *ordering, waiting, retry, and state*; gates exist exactly once per path via a shared gate function.

---

## 8. Security & reliability audit (spec §8)

| Area | Status | Evidence (VERIFIED unless noted) |
|---|---|---|
| Tool permissions | STRONG | Order availability→consent `main.py:1573-1577`; fail-closed metadata `main.py:1643-1659`; closed vocab `permissions.py:29-38` |
| Confirmation | STRONG | Non-forgeable (UI-only `resolve`, test-enforced `test_tool_metadata.py:263-279`); 90 s expiry; single slot |
| Destructive operations | PARTIAL | `game_updater.py:635` `shutdown /s`, `computer_settings` irreversible set confirm-gated; **`desktop.py` exec = `requires_confirmation: False` (`:532`) with `exec(compile(...))` at `:99`** |
| External communication | PARTIAL | Egress allowlist observed: googleapis, elevenlabs, pollinations, search engines, cdnjs, pytorch, social domains; dashboard binds `0.0.0.0` (`server.py:937,964`) |
| Secret management | WEAK | Plaintext `config/api_keys.json`; **6 independent key readers**; `ui.py:6911-6916` overwrites the whole file on re-setup (wipes other settings — merge-write exists at `config_manager.py:20-34` but isn't used there) |
| Prompt-injection boundaries | PARTIAL | Model can't forge confirmation (structural); but model-generated code executes (`desktop.py:99`) under a restricted-builtins sandbox with real `ctypes`/`winreg` on Windows (`:68-78`); "no subprocess/import" rules are prompt-level (`:133-139`) |
| Arbitrary code execution | RISK | `desktop.py:99` (caution, no confirm); `dev_agent.py:287` `shell=True`; `dashboard/server.py:187` elevated firewall `.bat` with `shell=True` |
| Workflow execution limits | STRONG | Node/edge/timeout/retry caps `schema.py:100-108`; loop budget `runtime.py:296`; idempotency for dangerous nodes `:482-493` |
| Verification | PARTIAL | See §1#13; verdict propagation gap in automation (§6) |
| Cancellation | IMPLEMENTED | Runtime cancel/pause/resume `runtime.py:165-194`; dashboard emergency-stop `server.py:870` |
| Retry safety | PARTIAL | Dangerous/forbidden never auto-retried `runtime.py:411-412`; dispatcher never retries (by design); **`shutdown_jarvis` via automation bypasses fail-closed user-turn rule (§6 leak 2)** |
| Idempotency | IMPLEMENTED (automation) | sha256 tool+args keys, 200-key cap `store.py:23`; absent at dispatcher level |
| Audit logging | WEAK | `safety_manager.log_action`/`check_rate_limit` have **zero production callers**; `app_state` history exists but `add_listener` never registered; event bus emissions sparse |

**Concrete risks ranked (PROPOSED priority for remediation planning):**
1. `exec()` of model code without confirmation (`desktop.py:99,532`).
2. Dashboard `0.0.0.0` + auto-elevated firewall rule via `shell=True`.
3. Verifier-failed automation step records success (§6 leak 1) — honesty bug, not security.
4. Config overwrite on re-setup losing settings (`ui.py:6911-6916`).
5. Plaintext key + no logging configuration (forensics gap).

**Do not weaken existing boundaries** — the confirm/permission/fail-closed triangle is the system's crown jewels and is test-enforced.

---

## Dormant-code register (implemented, never instantiated outside tests)

`core/planner.py`, `core/task_graph.py`, `core/reasoning.py`, `core/replanner.py`, `core/self_checker.py`, `core/tool_registry.py`, `core/memory_v2.py`, `core/llm_client.py` (586-line Ollama/OpenAI client, no config keys), `core/installer.py`, `add_short_term_turn` buffers, `session_memory` slot (always `None`), `PLANNING`/`OBSERVING` states, most `EventType` values, `LiveLedger` (test-only), `GeminiImagenProvider` (honest stub), `safety_manager` rate-limit/audit.

## Prompt-vs-code discrepancies (never describe planned functionality as implemented)

1. `core/prompt.txt:136` references "the planning agent" — no such tool exists.
2. `core/safety_manager.py:15-16` promises rate limiting + audit trail — no production callers.
3. `runtime.py:8-9` promises verifier agreement gates node success — it does not (§6).
4. `risk.py:10-11` claims "pauses on dangerous" — no such code (only no-retry + idempotency).
5. `main.py:1691` promises `VERIFYING` state from verification — `_run_verification` never emits it; only `generate_image` does.
6. `docs/architecture/part-4/5/10/11-*.md` describe capabilities that are test-only.
