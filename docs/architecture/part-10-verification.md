# Part 10 — Verification Engine

## 10.1 Verification Principle

XYRIS must NEVER claim success merely because an action was issued.
Verification is a first-class intelligence capability.

## 10.2 Verification Loop

```
ACTION PERFORMED
  |
EXPECTED RESULT: What should have happened?
  |
OBSERVE: Capture actual state
  |
COMPARE: Expected vs actual
  |
VERIFIED?
  YES -> Store success trajectory
  NO  -> Classify discrepancy -> Replan or report
  UNVERIFIABLE -> Flag as unverified, never claim success
```

## 10.3 Verification Strategies by Tool Type

| Tool Type | Verification Method |
|-----------|-------------------|
| Open app | Check window exists in window list |
| File create | Check file exists + content matches |
| File edit | Diff before/after |
| Send message | Check message in sent items |
| Web search | Check results returned |
| Run command | Check exit code + output |
| Browser action | Check page state/DOM |
| System setting | Read setting value, compare |

## 10.4 False-Success Prevention

CRITICAL: False-success rate must be < 1%.

Rules:
- Never claim "done" without observation
- If verification is impossible, say "I attempted X but could not verify"
- If partially verified, report what was confirmed and what was not
- Verification failure triggers replanning, not silent success

## 10.5 V2 Status

| Aspect | V2 Status |
|--------|-----------|
| Verification engine | MISSING |
| Verification strategies | MISSING |
| False-success prevention | MISSING |
| Post-action observation | MISSING |
