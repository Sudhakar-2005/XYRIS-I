# Part 14 — Training Data Strategy

## 14.1 Data Categories

| Category | Description | Volume |
|----------|-------------|--------|
| Instruction following | General task instructions | 100K |
| Conversation | Multi-turn dialogues | 50K |
| Reasoning tasks | Logic, math, analysis | 50K |
| Coding | Code generation, debugging | 100K |
| Tool use | Tool selection + arguments | 200K |
| Planning | Goal decomposition | 50K |
| Computer use | UI interaction trajectories | 100K |
| Browser trajectories | Web navigation | 50K |
| Error recovery | Failure handling patterns | 50K |
| Verification | Post-action verification | 50K |
| Multimodal | Screenshot + description pairs | 100K |
| Personal assistant | Calendar, email, reminders | 50K |

## 14.2 Data Quality Rules

1. Every example must have verified outcome
2. No hallucinated data (all facts checked)
3. No private user data without explicit consent
4. No credentials, API keys, or sensitive information
5. Deduplicated across categories
6. Balanced across difficulty levels
7. Versioned with provenance tracking

## 14.3 Privacy Pipeline

```
RAW DATA
  |
PII DETECTION: Remove names, addresses, phone numbers
  |
CREDENTIAL REMOVAL: Remove all keys, tokens, passwords
  |
SENSITIVE FILTERING: Remove private conversations
  |
ANONYMIZATION: Replace user IDs with placeholders
  |
QUALITY SCORING: Rate example quality (1-5)
  |
HUMAN REVIEW: Review high-value examples
  |
DATASET: Final training dataset
```

## 14.4 Never Train On

- Private user conversations without consent
- Financial data (bank details, transactions)
- Health data
- Authentication credentials
- Internal system prompts or configurations
- Data that could enable harm
