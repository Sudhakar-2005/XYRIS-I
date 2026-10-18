# Part 18 — User Interface

## 18.1 UI Components

```
MAIN WINDOW
  |
HEADER BAR
  - Connection status indicator
  - Model selector
  - Persona indicator
  - Hotkey status
  |
CHAT AREA
  - Message history
  - Streaming responses
  - Code blocks (syntax highlighted)
  - Rich media (images, links)
  |
INPUT BAR
  - Text input
  - Voice toggle (PTT mode)
  - Send button
  |
DRAWER (right panel)
  - Memory browser
  - Session history
  - Tools registry
  - Persona selector
  - Settings
  |
STATUS BAR
  - Connection status
  - Token usage
  - Current model
```

## 18.2 Voice Interface

- Push-to-Talk: Ctrl+Alt+J (configurable)
- Wake word detection: "Hey XYRIS" (future)
- Barge-in: Interrupt model with voice
- TTS voice selection: Persona-mapped
- Voice activity detection (VAD)
- Noise reduction (background)

## 18.3 Accessibility

- Keyboard navigation for all UI elements
- Screen reader compatibility
- High contrast mode
- Configurable font sizes
- Color-blind friendly palette

## 18.4 V2 Status

| Aspect | V2 Status |
|--------|-----------|
| Main window | IMPLEMENTED (PyQt6) |
| Chat area | IMPLEMENTED |
| Voice toggle | IMPLEMENTED |
| Push-to-talk | IMPLEMENTED |
| Barge-in | IMPLEMENTED |
| Persona indicator | IMPLEMENTED |
| Session history | IMPLEMENTED |
| Memory browser | PARTIAL |
| Accessibility | MISSING |
