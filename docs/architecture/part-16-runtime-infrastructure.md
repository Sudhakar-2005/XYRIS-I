# Part 16 — Runtime Infrastructure

## 16.1 Runtime Components

```
AETHER RUNTIME (Python 3.11)
  |
EVENT BUS
  - Async message queue
  - Event types: USER_INPUT, TOOL_RESULT, SYSTEM_EVENT
  - Priority: REALTIME > NORMAL > BACKGROUND
  |
TOOL REGISTRY
  - Built-in tools (hardcoded)
  - Actions (auto-discovered)
  - Plugins (auto-discovered)
  - Schema validation
  |
SECURITY MANAGER
  - Permission checks
  - Risk classification
  - Confirmation gates
  - Audit logging
  |
MEMORY MANAGER
  - Working memory (in-memory)
  - Episodic (JSON files)
  - Semantic (JSON files)
  - Procedural (JSON files)
  |
PERCEPTION MANAGER
  - Screenshot capture
  - Screen reader
  - Camera
  - System state
  |
EXECUTOR
  - Tool execution
  - Timeout management
  - Error handling
  - Undo tracking
  |
VERIFIER
  - Post-action observation
  - Success/failure judgment
  - Replanning trigger
```

## 16.2 Execution Model

- Single event loop (asyncio)
- Tools execute in worker threads
- UI runs in main thread (PyQt6)
- Model API calls are async
- Perception capture is async
- Verification is synchronous after action

## 16.3 Resource Management

- Max concurrent tool executions: 3
- Max memory budget: 2GB (model + runtime)
- Max context window: 128K tokens
- Max memory tokens in context: 4000
- Tool execution timeout: 30s default
- Network request timeout: 30s
- Screenshot capture: On-demand only

## 16.4 V2 Status

| Aspect | V2 Status |
|--------|-----------|
| Event bus | MISSING (direct calls) |
| Tool registry | IMPLEMENTED (action_loader + plugin_loader) |
| Security manager | IMPLEMENTED (safety_manager.py) |
| Memory manager | IMPLEMENTED (memory_manager.py) |
| Perception manager | PARTIAL (screen_process, webcam) |
| Executor | PARTIAL (ActionExecutor) |
| Verifier | MISSING |
| Resource limits | PARTIAL (some hardcoded) |
