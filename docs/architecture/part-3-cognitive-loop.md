# Part 3 — Cognitive Loop

## 3.1 Canonical Loop

```
USER INPUT
    |
UNDERSTAND          -> Intent {goal, entities, constraints, confidence}
    |
CONTEXT             -> Gather relevant memory, environment state
    |
REASON              -> ReasoningResult {conclusion, alternatives, assumptions}
    |
PLAN                -> TaskGraph {tasks, dependencies, risks}
    |
SELECT ACTION       -> ToolCall {tool, args, risk_level}
    |
PERMISSION CHECK    -> Runtime: allowed? confirmed?
    |
EXECUTE             -> ActionResult {output, exit_code, side_effects}
    |
OBSERVE             -> EnvironmentState (post-action)
    |
VERIFY              -> VerificationResult {verified, confidence, discrepancy}
    |
SUCCESS?
   / \
 NO   YES
 |     |
REPLAN  REMEMBER (store trajectory)
 |       |
EXECUTE   RESULT -> USER
```

## 3.2 Failure Handling

| Event | Response |
|-------|----------|
| Tool fails | Classify error, attempt recovery, max 3 retries |
| Network fails | Retry with backoff, notify user after 30s |
| App unavailable | Suggest alternative, ask user |
| Permission denied | Report to user, do not retry silently |
| UI changes | Re-perceive, replan from observation |
| User interrupts | Cancel current action, preserve progress |
| Model uncertain | Present alternatives, ask user |
| Verification fails | Replan, do not claim success |
| Multiple solutions | Present options, let user choose |
| Task impossible | Report clearly, suggest alternatives |
| Contradictory info | Flag contradiction, ask user to resolve |

## 3.3 Retry Budget

- Max retries per action: 3
- Max replans per goal: 5
- Timeout per action: 30s default, configurable
- Escalation: After 3 failures -> ask user
- Never retry forbidden actions
- Never retry without observing the failure

## 3.4 Interrupt Handling

User interrupts mid-execution:
1. Stop current tool execution (if safely stoppable)
2. Preserve completed subtasks
3. Update task graph state
4. Acknowledge interruption
5. Wait for new instruction
6. Resume or replan based on new input
