# Part 5 — Planner + Task Graph

## 5.1 Data Structures

```python
@dataclass
class Task:
    id: str
    description: str
    action: str | None          # tool name, or None if planning only
    arguments: dict
    dependencies: list[str]     # task IDs that must complete first
    risk_level: Risk            # SAFE / CAUTION / DANGEROUS
    confirmation_needed: bool
    reversible: bool
    verification: str | None    # how to verify this task
    status: TaskStatus          # PENDING / RUNNING / DONE / FAILED / SKIPPED
    result: ActionResult | None
    retry_count: int
    max_retries: int

@dataclass
class TaskGraph:
    goal: str
    tasks: list[Task]
    created_at: datetime
    status: GraphStatus         # PLANNING / EXECUTING / COMPLETED / FAILED
    current_task: str | None    # task ID
    completed: list[str]        # completed task IDs
    failed: list[str]           # failed task IDs

class Risk(Enum):
    SAFE = "safe"
    CAUTION = "caution"
    DANGEROUS = "dangerous"
    FORBIDDEN = "forbidden"

class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"
```

## 5.2 Planner Pipeline

```
GOAL
  |
DECOMPOSE:
  1. Identify sub-goals
  2. For each sub-goal, identify required actions
  3. Map actions to available tools
  4. Identify dependencies between tasks
  5. Identify parallelizable tasks
  6. Assess risk for each task
  7. Determine confirmation requirements
  8. Plan verification strategy per task
  |
EXECUTE:
  For each task in dependency order:
    1. Check preconditions
    2. Request confirmation if needed
    3. Execute
    4. Observe result
    5. Verify
    6. If fail -> replan
    7. Update task graph
```

## 5.3 Dynamic Replanning

When a task fails:
1. Classify the failure
2. Check if failure is recoverable
3. If yes: generate alternative plan from current state
4. If no: report to user with completed progress
5. Never discard completed subtasks
6. Never retry the exact same failing action more than 3 times

## 5.4 Simple vs Complex Tasks

| Complexity | Example | Planner Response |
|-----------|---------|-----------------|
| Trivial | "What time is it?" | Direct tool call, no graph |
| Simple | "Open Paint" | Single task, no dependencies |
| Moderate | "Search for flights to Tokyo" | 2-3 tasks, linear |
| Complex | "Plan my trip to Tokyo" | Full task graph with branches |
| Ambiguous | "Help me with my project" | Ask for clarification first |

## 5.5 V2 Status

| Aspect | V2 Status |
|--------|-----------|
| Task graph | MISSING |
| Dependency tracking | MISSING |
| Dynamic replanning | MISSING |
| Verification strategy | MISSING |
| Risk assessment | PARTIAL (SafetyManager has risk levels) |
| Confirmation | IMPLEMENTED (confirm.py) |
| Undo | IMPLEMENTED (undo.py) |
