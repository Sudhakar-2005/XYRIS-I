# BRAIN V0.1 — Cognitive Gap Analysis (spec §3)

Method: for each proposed cognitive component — does anything in the codebase already do this job? Classification = **IMPLEMENTED / PARTIAL / MISSING / DORMANT**. Recommendation uses only four verbs: **reuse, extend, consolidate, replace**. Evidence tags per `BRAIN_AUDIT.md` legend. No new module is proposed where an existing one can do the job.

## Summary

| # | Component | Classification | Verdict (verb) |
|---|---|---|---|
| A | Intent Engine | PARTIAL | consolidate (prompt routing + keyword hooks; no monolith) |
| B | Context Engine | PARTIAL | extend (per-turn refresh on memory writes) |
| C | Reasoning Engine | DORMANT (defective) | replace (model-driven chain-of-thought stays authoritative; retire `core/reasoning.py`) |
| D | Planning Engine | PARTIAL | consolidate (model chaining + automation generator; retire/fix `core/planner.py`, never two planners) |
| E | Decision Engine | IMPLEMENTED (scattered) | consolidate (single shared gate function; kill adapter mirror) |
| F | Memory Intelligence | PARTIAL | extend (atomicity, dedupe, relevance eval before adoption of `memory_v2`) |
| G | Capability Intelligence | PARTIAL (buggy) | reuse + fix (`capability.py:84-88`) |
| H | Execution Intelligence | IMPLEMENTED | reuse (already one spine; close gate drift) |
| I | Verification Intelligence | PARTIAL | extend (structured verdicts + propagate into automation) |
| J | Recovery Intelligence | PARTIAL | reuse (automation retry ladder + connection/model ladders) |
| K | Learning & Evaluation | MISSING | build (eval harness first, then stats consumption) |
| L | Model Abstraction Layer | PARTIAL | extend (`gemini.call` funnel + dormant `llm_client.py`) |

---

## A. Intent Engine — PARTIAL → **consolidate**

**Exists (VERIFIED):** model tool-calling as router (`core/prompt.txt:28-32`); persona keyword switch (`main.py:1143-1176`); code-mode intent (`actions/code_helper.py:121`); automation intent→graph parse (`core/automation/generator.py:233`, clarify/draft/validate loop `:355`); proactive trigger scoring (`main.py:2668-2705`).

**Missing (VERIFIED):** no pre-model classifier, no confidence, no explicit routing state, no fallback when the model misroutes (nothing measures misroutes today).

**Recommendation:** do **not** build a monolith classifier. **Extend** the existing seams: (1) log every `function_call` + prompt intent as labeled data (feeds EVALUATION §M2); (2) add a cheap deterministic fast-path only where the model demonstrably fails (e.g., persona switch already handles its case in code). Promotion to a real router requires measured misroute rate — that's Phase 8, not Phase 1.

## B. Context Engine — PARTIAL → **extend**

**Exists (VERIFIED):** per-connection assembly `_build_config` (`main.py:1379-1492`); prompt budgets (`main.py:263-291`, `memory_manager.py:41-45`); four injection channels (vision `:2108`, proactive `:2668`, monitors `:2613`, briefing `:2443`); sliding window on the server side (`main.py:1463-1470`).

**Gap (VERIFIED):** assembly runs once per connection; `remember`/`forget` during a session don't reach context until reconnect (`main.py:468-476`); `session_memory` slot hardcoded `None` (`main.py:1812`).

**Recommendation:** **extend** — on memory write, append a bounded `live_injections` line (the channel already exists and is test-covered by the vision/proactive paths). No new subsystem. Re-evaluate after EVALUATION §M4.

## C. Reasoning Engine — DORMANT, defective → **replace (retire)**

**Exists:** `core/reasoning.py:82` — tests-only; template conclusions `:132-140` ("requires further specific logic implementation"), `_check_contradictions` always `[]` `:179-180`, `self_check` can never pass.

**Recommendation:** **replace** — the live reasoning engine *is* the Gemini model's function-call chain (`main.py:2291-2300`). Mark `core/reasoning.py` for deletion in Phase 2 (with `self_checker.py` placeholders) rather than wiring it: wiring a stub that can never self-check would violate "never describe planned functionality as implemented" in the worst way — it would fake a gate. **Do not build a local chain-of-thought engine**; V0.1's budget is integration, not model research.

## D. Planning Engine — PARTIAL → **consolidate**

**Exists (VERIFIED):** model-driven implicit plans (multi-step function calls); explicit graphs from the automation generator (`generator.py:355` → `validate.py`); dormant `core/planner.py:62` (defective: duplicate ids `:197-228`, string risk comparison `:373`, validate-failure only logged `:131-132`); dormant `core/task_graph.py` with cycle check but **no executor**.

**Recommendation:** **consolidate on two proven producers** — (1) the model's implicit chaining for chat, (2) the automation generator for anything that must be persisted/approved/scheduled. `core/planner.py` → **fix-then-wire or delete in Phase 4**, decision gated on EVALUATION §M3 (does model chaining fail on >2-step tasks? if no, delete). `task_graph.py` stays superseded by `automation/schema.py` — one graph format only. Unify naming: the Tool Registry defines capability; only the generator may create nodes referencing it (`validate.py:155-162` already enforces this for automation).

## E. Decision Engine — IMPLEMENTED but scattered → **consolidate**

**Exists (VERIFIED):** `_execute_tool` gates (`main.py:1547-1641`), `PermissionLedger` fail-closed (`permissions.py:88`), non-forgeable confirm (`confirm.py:82/:115`, UI-only `ui.py:6766`), forbidden-list before everything (`main.py:1560`), automation mirror (`adapters.py:128-171`), automation risk analyzer (`risk.py`), plugin policy default (`main.py:1653-1654`).

**Gap (VERIFIED):** the mirror has **already drifted** (missing `shutdown_jarvis` fail-closed, AUDIT §6 leak 2); four risk vocabularies untranslated; plugin confirmation analyzed but never enforced at runtime.

**Recommendation:** **consolidate** — extract `_execute_tool`'s gate block into one pure function (e.g., `core/gates.py: evaluate_gates(tool_name, meta, ledger) → allow|deny|confirm`) called by both `_execute_tool` and `adapters.py`. Deletion of the mirrored copy is the acceptance test (grep shows one implementation). Add the plugin-confirmation enforcement in the same change. This is the **highest-value consolidation in the system** because it converts a proven-drifted duplicate into a tested single source.

## F. Memory Intelligence — PARTIAL → **extend**

Covered in detail: `BRAIN_AUDIT.md` §4. Direction: **atomic writes first** (reuse `store.py:57-65` pattern), dedupe keys, session-summary cap configurable; adopt `core/memory_v2.py` retrieval **only if** EVALUATION §M1 shows lexical search failing on a labeled set. Embeddings/vector store: **not proposed for V0.1** — the deliberate zero-cost design (`memory_manager.py:327-329`) is a feature, and no measurement yet justifies an external dependency.

## G. Capability Intelligence — PARTIAL, buggy → **reuse + fix**

**Exists (VERIFIED):** `CapabilityCatalog` refresh from live registries (`main.py:873-875`, `core/automation/capability.py`); validation consumes it (`validate.py:155-162`).

**Defect (VERIFIED):** `capability.py:84-88` does `records = getattr(self._actions, "_all_records", None)` then `isinstance(records, dict)` — but `_all_records` is a **list** (`action_loader.py:180`) → per-action parameter schemas/availability never reach the catalog → `validate.py:166` required-param check inert for action tools.

**Recommendation:** **reuse** the catalog; fix the reader to iterate the list (or add a dict view in `ActionRegistry`). Acceptance: a generated workflow referencing a required-param-missing action fails validation with the parameter named. Also **extend** with a translation table for the four risk vocabularies (E).

## H. Execution Intelligence — IMPLEMENTED → **reuse**

**Exists:** single spine `_dispatch_tool` (`main.py:1675`), tool timeouts (`:1740-1768`), automation runtime retry/idempotency/pause/approval/dry-run (`runtime.py`), policy fail-closed (`main.py:1643-1659`), events with `correlation_id` (`main.py:2819-2828`).

**Recommendation:** **reuse** as-is; the only change is inheriting E's single-gate refactor. Do not add dispatcher-level retries (explicit design decision, `core/prompt.txt:131`).

## I. Verification Intelligence — PARTIAL → **extend**

**Exists (VERIFIED):** strategy framework (`verifier.py:29-39`), wiring (`main.py:593,616,1854,1886`), honest stubs (`:350,:359`), automation's independent assertion engine (`runtime.py:883`), 3 tools emitting CUSTOM checks (`actions/browser_control.py` note: DOM stub `:1091`).

**Gaps (VERIFIED):** verdict is a **prose note** (`main.py:608-610`) that (a) nothing enforces, (b) automation's `looks_like_failure` doesn't match (`adapters.py:26-33`), (c) the `VERIFYING` state has no emitter (`app_state.py:12-14`).

**Recommendation:** **extend** — make `_run_verification` return a structured verdict `{passed, strategy, detail}`; dispatch stores it on the result envelope; automation's adapter reads it (this closes AUDIT §6 leak 1). Then widen coverage one tool at a time where a cheap CUSTOM check exists (each new strategy gets a test). Do not implement DOM/SCREENSHOT strategies until a browser-automation need is measured — the stubs are the honest state.

## J. Recovery Intelligence — PARTIAL → **reuse**

**Exists (VERIFIED):** automation node retry with jitter + dangerous-node no-retry (`runtime.py:410-438`), idempotency (`:482-493`), cancel/pause (`:165-194`); connection exponential backoff (`main.py:3277-3289`); model ladder on quota (`model_manager.py:117-138`); `core/undo.py`; model-mediated retry via failure text.

**Dormant:** `core/replanner.py` keyword classifier (tests-only).

**Recommendation:** **reuse** the three real ladders; treat replanner as **candidate for Phase 5 only after** its classifier is validated against labeled failure logs (EVALUATION §M6). No generic recovery engine in V0.1.

## K. Learning & Evaluation — MISSING → **build (evaluation first)**

**Exists:** `RungStats` collected (`model_manager.py:67-86`), automation run statistics (`store.update_statistics`), event bus, `core/perf.py` marks, 1,454 tests as regression baseline.

**Missing:** any harness that scores behavior (intent routing, tool selection, planning, memory relevance, recovery). No metric feeds back anywhere.

**Recommendation:** build `EVALUATION_PLAN.md`'s harness **before** any cognitive wiring (Phase 8 in roadmap but harness scaffolding lands in Phase 1 so acceptance criteria of later phases can be measured). Learning loop (adjusting tiers/memory from stats) is V0.2 — record as PROPOSED, not implemented.

## L. Model Abstraction Layer — PARTIAL → **extend**

**Exists (VERIFIED):** one one-shot funnel `core/gemini.py:319` used by every background feature; `ModelManager` tiers + ladder + cooldown (`model_manager.py:58-62,117-201`); `LIVE_SLOTS` resource cap (`:47`); dormant `core/llm_client.py` (Ollama/OpenAI HTTP, 586 lines, zero callers, no config keys).

**Gap:** the Live conversation path is Gemini-only (`main.py:3139-3146`); no provider metadata (context window, tool support) beyond tier labels.

**Recommendation:** **extend** — (1) define the call interface `call(text|messages, tools?, json?) → stream|result` = what `llm_client.py` already implements for OpenAI/Ollama; (2) make `core/gemini.py:319` the reference adapter; (3) adopt `llm_client.py` as second adapter after a health-check + timeout review (its absence from config is a gap, not a feature). Keep model **independence** (multi-provider) strictly separate from model **training/fine-tuning** (out of scope) — see TARGET_ARCHITECTURE §7.

---

## Explicit non-goals for V0.1 (evidence-based)

- No local reasoning engine replacing the model (C).
- No second general planner alongside model chaining + generator (D).
- No embeddings/vector DB until lexical retrieval fails a labeled test (F).
- No DOM/screenshot verifier until a real consumer exists (I).
- No dispatcher-level retries (H — deliberate design).
- No native/performance rewrite of the Python core (native = V0.2+, PROPOSED).
