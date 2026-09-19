# Part 19 — Deployment & Operations

## 19.1 Deployment Model

- Local-first: Runs on user's machine
- No cloud dependency (except model API)
- Data stays on user's device
- Model API: Gemini (current), future local models

## 19.2 Configuration

- config/api_keys.json: API credentials
- config/settings.json: User preferences
- memory/long_term.json: Identity, persona, memories
- run.bat: Windows launcher
- requirements.txt: Python dependencies

## 19.3 Monitoring

- Console logs with timestamps
- Log levels: DEBUG, INFO, WARNING, ERROR, CRITICAL
- Performance metrics: response time, token usage
- Error tracking: Exception logging with stack traces
- Health checks: Connection status, memory usage

## 19.4 Update Strategy

- Manual updates via git pull
- Dependency updates via pip
- Configuration migration on version change
- Backward compatibility for memory files

## 19.5 Backup & Recovery

- memory/ directory: User data
- config/ directory: Settings
- Automatic backup before major changes
- Restore from backup on corruption
