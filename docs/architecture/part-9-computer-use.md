# Part 9 — Computer Use

## 9.1 Computer Use Loop

```
OBSERVE     -> Capture screenshot + window state + a11y tree
UNDERSTAND  -> Identify target element, current state, available actions
PLAN        -> Determine action sequence
ACT         -> Execute via pyautogui or accessibility API
OBSERVE     -> Verify the screen changed as expected
VERIFY      -> Confirm action had intended effect
```

## 9.2 Interaction Strategies (Priority Order)

1. Accessibility API (preferred) - Windows UI Automation
2. Semantic targeting - Match UI element names/roles
3. DOM manipulation - For web content via Playwright
4. Coordinate-based (last resort) - pyautogui with screenshot verification

## 9.3 V2 Status

| Aspect | V2 Status |
|--------|-----------|
| Screenshot capture | IMPLEMENTED |
| pyautogui control | IMPLEMENTED |
| Playwright browser | IMPLEMENTED |
| Accessibility API | MISSING |
| Semantic targeting | MISSING |
| Computer use loop | MISSING (no verify-after-act) |
