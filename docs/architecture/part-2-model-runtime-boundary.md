# Part 2 — Model vs Runtime Boundary

## 2.1 Architecture Layers

```
USER INTERFACE
  Voice / Text / Screen / Camera / Dashboard
        |
XYRIS FOUNDATION MODEL
  Language / Reasoning / Planning / Code / Tool Selection / Multimodal
  [Future: XYRIS-native]  [Current: Gemini Live API]
        |
XYRIS COGNITIVE LAYER
  Intent Parser / Context Manager / Reasoning Structurer
  Planner / Verifier / Identity Manager
  [Deterministic + hybrid]  [Python, calls model]
        |
XYRIS AGENT RUNTIME
  Executor / Tool Registry / Security Manager
  Undo Stack / Event Bus / Transaction Log
  Confirmation Gate / Rate Limiter
  [100% deterministic]  [Never delegated to model]
        |
XYRIS TOOLS
  Actions / Plugins / OS Interface / Browser / Files / Network
        |
XYRIS OS INTERFACE
  Windows API / File System / Process / Audio / Display / Network
```

## 2.2 Capability Assignment Matrix

| Capability | Model | Cognitive | Runtime | Tool | Rationale |
|-----------|-------|-----------|---------|------|-----------|
| Language understanding | PRIMARY | Validate | - | - | Neural comprehension |
| Intent parsing | PRIMARY | Validate | - | - | NL to structured intent |
| Reasoning | PRIMARY | Structure | - | - | Inference capability |
| Planning | PRIMARY | Validate | - | - | Goal decomposition |
| Context management | - | PRIMARY | - | - | Deterministic data flow |
| Memory retrieval | - | PRIMARY | Query | - | Scored retrieval |
| Memory storage | - | PRIMARY | Write | - | Deterministic persistence |
| Tool selection | PRIMARY | Validate | Registry | - | Semantic matching |
| Tool execution | - | - | PRIMARY | Dispatch | Deterministic, auditable |
| Argument validation | - | Validate | PRIMARY | - | Schema validation |
| Risk classification | - | PRIMARY | - | - | Rules + model assist |
| Permission checking | - | - | PRIMARY | - | Non-bypassable |
| Confirmation | - | - | PRIMARY | - | Deterministic gate |
| Verification | - | PRIMARY | Observe | - | Hybrid |
| Rollback | - | - | PRIMARY | Undo | Deterministic |
| Error recovery | PRIMARY | PRIMARY | Execute | - | Model plans, runtime executes |
| Computer vision | PRIMARY | Structure | - | - | Neural perception |
| UI grounding | PRIMARY | Structure | A11y | - | Vision + accessibility |
| Browser control | - | - | Dispatch | Playwright | Deterministic |
| File operations | - | - | Dispatch | FileCtrl | Deterministic + safety |
| OS commands | - | - | Dispatch | SafetyMgr | Restricted, confirmed |
| Voice recognition | - | - | PRIMARY | STT | Deterministic pipeline |
| Speech synthesis | - | - | PRIMARY | TTS | Deterministic pipeline |
| Personality | PRIMARY | Apply | - | - | Via prompt |
| Identity | - | PRIMARY | - | - | Deterministic rules |
| Safety enforcement | - | - | PRIMARY | - | NEVER model-dependent |
| Audit logging | - | - | PRIMARY | - | Append-only |

## 2.3 Critical Rules

**Rule 1: Security is NEVER model-dependent.**
The model can SUGGEST an action is safe. The runtime DECIDES.
The model never bypasses permissions, confirmations, or safety checks.

**Rule 2: Verification is never self-reported.**
The model cannot verify its own actions. Verification requires
independent observation. If the model says "I did X", the runtime
must observe that X actually happened.

**Rule 3: Execution is always auditable.**
Every action goes through the Tool Registry. Every execution is logged.
The model cannot execute actions outside the registered tool set.

**Rule 4: Memory is never blindly trusted.**
Memory retrieval is scored by relevance, not assumed correct.
Conflicting memories are flagged, not silently resolved.

**Rule 5: The model is replaceable.**
The entire architecture must work with any capable LLM.
The Cognitive Layer and Runtime are model-agnostic.
No capability depends on a specific model's behavior.
