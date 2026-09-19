# Part 7 — Tool Intelligence

## 7.1 Tool Schema

Every tool must declare:

```python
@dataclass
class ToolSchema:
    name: str                    # Unique identifier
    description: str             # What it does
    parameters: dict             # JSON Schema for arguments
    risk_level: Risk             # SAFE / CAUTION / DANGEROUS / FORBIDDEN
    permissions: list[str]       # Required permissions
    timeout: int                 # Max execution time (seconds)
    confirmation_required: bool  # Needs user confirmation
    reversible: bool             # Can be undone
    verification_strategy: str   # How to verify success
    failure_modes: list[str]     # Known failure patterns
    scheduling: str              # BLOCKING / NON_BLOCKING / SILENT / INTERRUPT
```

## 7.2 Tool Selection Pipeline

```
INTENT
  |
DISCOVERY: Which tools could handle this intent?
  |
SCORING: Rank tools by semantic match to intent
  |
SELECTION: Pick best tool (model-assisted)
  |
VALIDATION: Check argument schema compliance
  |
RISK ANALYSIS: Classify risk level
  |
PERMISSION: Check if allowed
  |
CONFIRMATION: Ask user if required
  |
EXECUTION: Run tool with timeout
  |
VERIFICATION: Confirm expected outcome
  |
RESULT: Return structured result
```

## 7.3 Tool Registry

The Tool Registry is the single source of truth for all capabilities:
- Built-in tools: Hardcoded function declarations (system tools)
- Actions: Auto-discovered from actions/*.py
- Plugins: Auto-discovered from plugins/*.py

The model ONLY sees tools that are registered. It cannot invent tools.
The registry enforces schemas, permissions, and risk levels.

## 7.4 V2 Status

| Aspect | V2 Status |
|--------|-----------|
| Tool schema | PARTIAL (TOOL dict, no verification_strategy) |
| Tool discovery | IMPLEMENTED (action_loader, plugin_loader) |
| Risk classification | IMPLEMENTED (SafetyManager) |
| Confirmation | IMPLEMENTED (confirm.py) |
| Undo | IMPLEMENTED (undo.py) |
| Verification strategy | MISSING |
| Failure mode declaration | MISSING |
| Schema validation | PARTIAL (basic type checking) |
