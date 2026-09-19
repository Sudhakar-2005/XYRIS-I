"""
Task Graph — Data structures for planning and execution.

Part of AETHER-I Intelligence Architecture V0.1
Phase 1: Runtime Foundation
"""

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    """Status of a task."""
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class GraphStatus(Enum):
    """Status of a task graph."""
    PLANNING = "planning"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"


class Risk(Enum):
    """Risk level for tasks."""
    SAFE = "safe"
    CAUTION = "caution"
    DANGEROUS = "dangerous"
    FORBIDDEN = "forbidden"


@dataclass
class TaskResult:
    """Result of a task execution."""
    success: bool
    output: Any = None
    errors: list[str] = field(default_factory=list)
    duration_ms: float = 0.0
    verified: bool = False


@dataclass
class Task:
    """A single task in a task graph.

    Tasks represent atomic units of work that can be executed,
    verified, and retried independently.
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    description: str = ""
    action: Optional[str] = None  # tool name, or None if no tool needed
    arguments: dict = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)  # task IDs
    risk_level: Risk = Risk.SAFE
    confirmation_needed: bool = False
    reversible: bool = False
    verification_strategy: str = "none"
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[TaskResult] = None
    retry_count: int = 0
    max_retries: int = 3
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    metadata: dict = field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        """Check if task is in a terminal state."""
        return self.status in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.SKIPPED)

    @property
    def duration_ms(self) -> Optional[float]:
        """Get task duration in milliseconds, or None if not started."""
        if self.started_at is None:
            return None
        end = self.completed_at or time.time()
        return (end - self.started_at) * 1000

    def can_retry(self) -> bool:
        """Check if task can be retried."""
        return self.status == TaskStatus.FAILED and self.retry_count < self.max_retries

    def start(self) -> None:
        """Mark task as started."""
        self.status = TaskStatus.RUNNING
        self.started_at = time.time()

    def complete(self, result: TaskResult) -> None:
        """Mark task as completed with result."""
        self.status = TaskStatus.DONE if result.success else TaskStatus.FAILED
        self.result = result
        self.completed_at = time.time()

    def skip(self, reason: str = "") -> None:
        """Skip this task."""
        self.status = TaskStatus.SKIPPED
        self.result = TaskResult(success=False, errors=[f"Skipped: {reason}"])
        self.completed_at = time.time()

    def to_dict(self) -> dict:
        """Serialize task to dictionary."""
        return {
            "id": self.id,
            "description": self.description,
            "action": self.action,
            "arguments": self.arguments,
            "dependencies": self.dependencies,
            "risk_level": self.risk_level.value,
            "confirmation_needed": self.confirmation_needed,
            "reversible": self.reversible,
            "verification_strategy": self.verification_strategy,
            "status": self.status.value,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Task":
        """Deserialize task from dictionary."""
        return cls(
            id=data.get("id", str(uuid.uuid4())[:8]),
            description=data.get("description", ""),
            action=data.get("action"),
            arguments=data.get("arguments", {}),
            dependencies=data.get("dependencies", []),
            risk_level=Risk(data.get("risk_level", "safe")),
            confirmation_needed=data.get("confirmation_needed", False),
            reversible=data.get("reversible", False),
            verification_strategy=data.get("verification_strategy", "none"),
            status=TaskStatus(data.get("status", "pending")),
            retry_count=data.get("retry_count", 0),
            max_retries=data.get("max_retries", 3),
            created_at=data.get("created_at", time.time()),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            metadata=data.get("metadata", {}),
        )


@dataclass
class TaskGraph:
    """A graph of tasks with dependencies.

    Manages task execution order, dependency resolution,
    and progress tracking.
    """
    goal: str = ""
    tasks: list[Task] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    status: GraphStatus = GraphStatus.PLANNING
    completed: list[str] = field(default_factory=list)  # task IDs
    failed: list[str] = field(default_factory=list)  # task IDs
    metadata: dict = field(default_factory=dict)

    def add_task(self, task: Task) -> None:
        """Add a task to the graph."""
        self.tasks.append(task)

    def get_task(self, task_id: str) -> Optional[Task]:
        """Get a task by ID."""
        for task in self.tasks:
            if task.id == task_id:
                return task
        return None

    def remove_task(self, task_id: str) -> bool:
        """Remove a task by ID."""
        for i, task in enumerate(self.tasks):
            if task.id == task_id:
                self.tasks.pop(i)
                return True
        return False

    def get_ready_tasks(self) -> list[Task]:
        """Get tasks that are ready to execute (all dependencies done)."""
        ready = []
        for task in self.tasks:
            if task.status != TaskStatus.PENDING:
                continue

            # Check all dependencies are done
            deps_met = all(
                dep_id in self.completed
                for dep_id in task.dependencies
            )

            if deps_met:
                ready.append(task)

        return ready

    def get_blocked_tasks(self) -> list[Task]:
        """Get tasks that are blocked by incomplete dependencies."""
        blocked = []
        for task in self.tasks:
            if task.status != TaskStatus.PENDING:
                continue

            deps_met = all(
                dep_id in self.completed
                for dep_id in task.dependencies
            )

            if not deps_met:
                blocked.append(task)

        return blocked

    def get_failed_tasks(self) -> list[Task]:
        """Get all failed tasks."""
        return [t for t in self.tasks if t.status == TaskStatus.FAILED]

    def get_retryable_tasks(self) -> list[Task]:
        """Get failed tasks that can be retried."""
        return [t for t in self.tasks if t.can_retry()]

    def mark_task_done(self, task_id: str) -> None:
        """Mark a task as completed."""
        if task_id not in self.completed:
            self.completed.append(task_id)

    def mark_task_failed(self, task_id: str) -> None:
        """Mark a task as failed."""
        if task_id not in self.failed:
            self.failed.append(task_id)

    def update_status(self) -> None:
        """Update graph status based on task states."""
        all_done = all(t.is_terminal for t in self.tasks)
        any_failed = any(t.status == TaskStatus.FAILED for t in self.tasks)

        if all_done:
            if any_failed:
                self.status = GraphStatus.FAILED
            else:
                self.status = GraphStatus.COMPLETED
        elif any(t.status == TaskStatus.RUNNING for t in self.tasks):
            self.status = GraphStatus.EXECUTING

    @property
    def progress(self) -> float:
        """Get completion progress (0.0 to 1.0)."""
        if not self.tasks:
            return 1.0
        done = sum(1 for t in self.tasks if t.is_terminal)
        return done / len(self.tasks)

    @property
    def total_tasks(self) -> int:
        """Total number of tasks."""
        return len(self.tasks)

    @property
    def completed_tasks(self) -> int:
        """Number of completed tasks."""
        return len([t for t in self.tasks if t.status == TaskStatus.DONE])

    @property
    def failed_tasks(self) -> int:
        """Number of failed tasks."""
        return len([t for t in self.tasks if t.status == TaskStatus.FAILED])

    def validate(self) -> list[str]:
        """Validate the task graph for issues.

        Returns list of error messages (empty if valid).
        """
        errors = []

        # Check for duplicate IDs
        ids = [t.id for t in self.tasks]
        if len(ids) != len(set(ids)):
            errors.append("Duplicate task IDs found")

        # Check for missing dependencies
        for task in self.tasks:
            for dep_id in task.dependencies:
                if dep_id not in ids:
                    errors.append(f"Task '{task.id}' depends on unknown task '{dep_id}'")

        # Check for circular dependencies
        visited = set()
        rec_stack = set()

        def has_cycle(task_id: str) -> bool:
            visited.add(task_id)
            rec_stack.add(task_id)

            task = self.get_task(task_id)
            if task:
                for dep_id in task.dependencies:
                    if dep_id not in visited:
                        if has_cycle(dep_id):
                            return True
                    elif dep_id in rec_stack:
                        return True

            rec_stack.discard(task_id)
            return False

        for task in self.tasks:
            if task.id not in visited:
                if has_cycle(task.id):
                    errors.append(f"Circular dependency detected involving task '{task.id}'")

        return errors

    def to_dict(self) -> dict:
        """Serialize task graph to dictionary."""
        return {
            "goal": self.goal,
            "tasks": [t.to_dict() for t in self.tasks],
            "created_at": self.created_at,
            "status": self.status.value,
            "completed": self.completed,
            "failed": self.failed,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TaskGraph":
        """Deserialize task graph from dictionary."""
        graph = cls(
            goal=data.get("goal", ""),
            created_at=data.get("created_at", time.time()),
            status=GraphStatus(data.get("status", "planning")),
            completed=data.get("completed", []),
            failed=data.get("failed", []),
            metadata=data.get("metadata", {}),
        )
        graph.tasks = [Task.from_dict(t) for t in data.get("tasks", [])]
        return graph
