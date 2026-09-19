# Part 6 — Memory 2.0

## 6.1 Memory Types

### Working Memory
- What: Current conversation context, active task state, temporary variables
- Why: Enables multi-turn reasoning within a session
- Lifetime: Single session
- Retrieval: Direct access (it IS the current context)
- Privacy: Ephemeral, never persisted unless explicitly saved
- Storage: In-memory Python objects

### Episodic Memory
- What: Past interactions, task outcomes, user preferences learned through experience
- Why: Enables learning from past successes and failures
- Lifetime: Persistent, with decay based on recency and relevance
- Retrieval: Query by keyword, time range, task type, outcome
- Privacy: User-controlled, PII-stripped, encrypted at rest
- Storage: JSON files in memory/ directory, structured by date

### Semantic Memory
- What: Facts about the world, user identity, project knowledge, tool capabilities
- Why: Provides foundational knowledge for reasoning
- Lifetime: Persistent, updated when contradictions found
- Retrieval: Query by topic, entity, relationship
- Privacy: Separated by sensitivity level
- Storage: JSON files, structured by category

### Procedural Memory
- What: How to perform tasks, successful action sequences, tool usage patterns
- Why: Enables efficient execution of repeated tasks
- Lifetime: Persistent, strengthened by repeated success
- Retrieval: Match task type to stored procedures
- Privacy: Never contains credentials or sensitive data
- Storage: JSON files, structured by task type

## 6.2 Memory Retrieval Pipeline

```
QUERY
  |
RETRIEVE: Search across all memory types
  |
SCORE: Relevance scoring (keyword match + recency + outcome quality)
  |
FILTER: Remove low-relevance, expired, contradictory entries
  |
SELECT: Budget-limited context selection (max tokens)
  |
INJECT: Add to model context as structured memory block
  |
MODEL PROCESSES
  |
FEEDBACK: Update relevance scores based on whether memory was useful
```

## 6.3 Memory Protection

- Poisoning defense: Cross-reference new memories against existing knowledge
- Contradiction detection: Flag when new memory conflicts with existing
- PII stripping: Remove phone numbers, addresses, passwords before storage
- Credential isolation: Never store API keys, tokens, passwords in memory
- Prompt injection defense: Sanitize memory content before context injection
- Context overload prevention: Hard budget on memory tokens in context
- User control: User can view, edit, delete any memory entry
- Versioning: Track memory changes for audit trail

## 6.4 V2 Status

| Aspect | V2 Status | Gap |
|--------|-----------|-----|
| Working memory | IMPLEMENTED | Via Gemini session state |
| Episodic memory | PARTIAL | Session summaries exist, no trajectory recording |
| Semantic memory | IMPLEMENTED | long_term.json with categories |
| Procedural memory | MISSING | No action sequence recording |
| Relevance scoring | PARTIAL | _score() in memory_manager.py |
| Budget management | IMPLEMENTED | 900 chars core + 420 chars index |
| Contradiction detection | MISSING | No cross-referencing |
| PII protection | PARTIAL | Basic filtering |
| User control | PARTIAL | update_memory exists, no full CRUD UI |
