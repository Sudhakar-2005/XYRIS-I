# Part 4 — Reasoning Architecture

## 4.1 Reasoning Modes

| Mode | Speed | Depth | Use Case |
|------|-------|-------|----------|
| Fast response | < 100ms | Shallow | Greetings, simple queries, confirmations |
| Deep reasoning | 1-10s | Deep | Complex problems, multi-step analysis |
| Planning reasoning | 2-15s | Very deep | Goal decomposition, task graph creation |
| Tool reasoning | 100ms-1s | Medium | Tool selection, argument construction |
| Verification reasoning | 100ms-2s | Medium | Result comparison, success judgment |
| Error reasoning | 500ms-5s | Deep | Failure diagnosis, recovery planning |

## 4.2 Reasoning Artifacts

XYRIS never exposes raw chain-of-thought. Instead, structured artifacts:

```
REASONING ARTIFACT
  TASK:           What I am trying to achieve
  ASSUMPTIONS:    What I believe to be true
  EVIDENCE:       What I know (from memory, perception, tools)
  PLAN:           What I intend to do
  RISKS:          What could go wrong
  CONFIDENCE:     How certain I am (0.0 - 1.0)
  ALTERNATIVES:   Other approaches I considered
  DECISION:       What I chose and why
```

## 4.3 Reasoning Pipeline

```
INPUT
  |
FAST CHECK: Is this trivial? -> YES -> Fast response
  |
DEEP REASONING:
  1. Parse intent from input
  2. Gather context (memory + perception)
  3. Identify what I know vs don't know
  4. If info missing -> retrieve or ask user
  5. Apply reasoning strategies:
     - Deduction (general to specific)
     - Induction (specific to general)
     - Analogy (similar past situations)
     - Elimination (rule out impossible)
  6. Generate conclusion with confidence
  7. Self-check: Does conclusion contradict known facts?
  8. Output structured reasoning artifact
```

## 4.4 Self-Checking

After every reasoning step, XYRIS must:
1. Check conclusion against known facts
2. Check for internal contradictions
3. Assess confidence honestly
4. If confidence < 0.5 -> present uncertainty to user
5. Never fabricate facts to support a conclusion

## 4.5 V2 Status

| Aspect | V2 Status | Gap |
|--------|-----------|-----|
| Fast response | IMPLEMENTED | Via Gemini Live streaming |
| Deep reasoning | PARTIAL | Model reasons, no structured artifact |
| Planning reasoning | MISSING | No task graph generation |
| Tool reasoning | PARTIAL | Model selects tools via declarations |
| Verification reasoning | MISSING | No post-action verification |
| Self-checking | MISSING | No confidence assessment |
| Structured artifacts | MISSING | No reasoning trace stored |
