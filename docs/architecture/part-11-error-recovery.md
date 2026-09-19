# Part 11 — Error Recovery

## 11.1 Error Classification

```
FAILURE
  |
CLASSIFY:
  - Invalid input (user provided bad data)
  - Missing tool (required tool not available)
  - Permission denied (security blocked action)
  - Network failure (connectivity issue)
  - Application failure (app crashed or unresponsive)
  - UI mismatch (expected UI not found)
  - Timeout (action took too long)
  - Unexpected output (result does not match预期)
  - Verification failure (action succeeded but verification failed)
  - Model uncertainty (confident enough to proceed)
  - Security violation (action blocked by safety)
  |
RECOVER?
  YES -> REPLAN from current state
  NO  -> REPORT to user with clear explanation
```

## 11.2 Recovery Strategies

| Error Type | Strategy | Max Retries |
|-----------|----------|-------------|
| Invalid input | Ask user for correction | 0 (ask once) |
| Missing tool | Suggest alternative tool | 1 |
| Permission denied | Report, suggest alternative | 0 |
| Network failure | Retry with backoff | 3 |
| Application failure | Restart app, retry | 2 |
| UI mismatch | Re-perceive, find target | 2 |
| Timeout | Retry once, then escalate | 1 |
| Unexpected output | Replan with different approach | 2 |
| Verification failure | Replan | 2 |
| Model uncertainty | Present options to user | 0 |
| Security violation | Report, do not retry | 0 |

## 11.3 Escalation Rules

- After 3 failures on same action -> ask user
- After 5 replans on same goal -> report partial progress
- Never silently give up
- Always report what was completed vs what failed
- Never hide failures behind success

## 11.4 V2 Status

| Aspect | V2 Status |
|--------|-----------|
| Error classification | MISSING |
| Recovery strategies | PARTIAL (basic retry exists) |
| Escalation rules | MISSING |
| Partial progress reporting | MISSING |
