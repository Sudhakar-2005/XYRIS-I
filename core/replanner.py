"""
Replanner — Adaptive replanning when tasks fail.

Part of AETHER-I Intelligence Architecture V0.1
Phase 1: Runtime Foundation

The Replanner analyzes task failures, classifies them,
and generates recovery strategies or revised plans.
"""

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from core.task_graph import Task, TaskGraph, TaskResult, TaskStatus, Risk

logger = logging.getLogger(__name__)


class FailureType(Enum):
    """Classification of task failures."""
    NETWORK = "network"
    PERMISSION = "permission"
    APP = "app"
    UI = "ui"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


class RecoveryStrategy(Enum):
    """Recovery strategies for failures."""
    RETRY = "retry"
    RETRY_WITH_DELAY = "retry_with_delay"
    SKIP = "skip"
    ALTERNATIVE = "alternative"
    ASK_USER = "ask_user"
    ABORT = "abort"


@dataclass
class FailureAnalysis:
    """Analysis of a task failure."""
    failure_type: FailureType
    recovery_strategy: RecoveryStrategy
    confidence: float
    suggestion: str
    original_error: str = ""


@dataclass
class ReplanResult:
    """Result of replanning."""
    success: bool
    graph: Optional[TaskGraph] = None
    removed_tasks: list[str] = field(default_factory=list)
    added_tasks: list[str] = field(default_factory=list)
    analysis: Optional[FailureAnalysis] = None
    duration_ms: float = 0.0


class Replanner:
    """Handles adaptive replanning when tasks fail.

    The Replanner classifies failures, determines recovery strategies,
    and generates revised task graphs that preserve completed work.

    Usage:
        replanner = Replanner()
        analysis = replanner.analyze_failure(task, result)
        new_graph = replanner.replan(graph, failed_task)
    """

    # Error message patterns for classification
    NETWORK_KEYWORDS = {"connection", "network", "timeout", "dns", "refused", "unreachable", "internet"}
    PERMISSION_KEYWORDS = {"permission", "denied", "access", "forbidden", "unauthorized", "privilege"}
    APP_KEYWORDS = {"app", "application", "not found", "not installed", "missing", "executable"}
    UI_KEYWORDS = {"element", "button", "click", "selector", "not found", "visible", "display", "screen"}
    TIMEOUT_KEYWORDS = {"timeout", "timed out", "expired", "deadline", "slow"}

    def __init__(self):
        self._failure_count = 0
        self._recovery_count = 0
        self._replan_count = 0

    def classify_failure(self, error_message: str) -> FailureType:
        """Classify a failure based on the error message.

        Args:
            error_message: The error message from the failed task

        Returns:
            FailureType enum value
        """
        error_lower = error_message.lower()

        if any(kw in error_lower for kw in self.TIMEOUT_KEYWORDS):
            return FailureType.TIMEOUT
        if any(kw in error_lower for kw in self.NETWORK_KEYWORDS):
            return FailureType.NETWORK
        if any(kw in error_lower for kw in self.PERMISSION_KEYWORDS):
            return FailureType.PERMISSION
        if any(kw in error_lower for kw in self.UI_KEYWORDS):
            return FailureType.UI
        if any(kw in error_lower for kw in self.APP_KEYWORDS):
            return FailureType.APP
        return FailureType.UNKNOWN

    def determine_recovery(
        self, failure_type: FailureType, task: Task, retry_count: int = 0
    ) -> FailureAnalysis:
        """Determine the recovery strategy for a failure.

        Args:
            failure_type: The classified failure type
            task: The failed task
            retry_count: How many times this task has been retried

        Returns:
            FailureAnalysis with recovery strategy
        """
        self._failure_count += 1

        strategies = {
            FailureType.NETWORK: self._recover_network,
            FailureType.PERMISSION: self._recover_permission,
            FailureType.APP: self._recover_app,
            FailureType.UI: self._recover_ui,
            FailureType.TIMEOUT: self._recover_timeout,
            FailureType.UNKNOWN: self._recover_unknown,
        }

        return strategies[failure_type](task, retry_count)

    def analyze_failure(self, task: Task, result: TaskResult) -> FailureAnalysis:
        """Analyze a task failure and determine recovery.

        Args:
            task: The failed task
            result: The task result with error information

        Returns:
            FailureAnalysis with classification and recovery strategy
        """
        error_message = "; ".join(result.errors) if result.errors else "Unknown error"
        failure_type = self.classify_failure(error_message)
        analysis = self.determine_recovery(failure_type, task, task.retry_count)
        analysis.original_error = error_message
        return analysis

    def replan(self, graph: TaskGraph, failed_task_id: str) -> ReplanResult:
        """Create a new plan after a task failure.

        Preserves completed tasks, removes the failed task,
        and adds alternative tasks if appropriate.

        Args:
            graph: The current task graph
            failed_task_id: ID of the task that failed

        Returns:
            ReplanResult with new graph
        """
        start_time = time.time()

        failed_task = graph.get_task(failed_task_id)
        if not failed_task:
            return ReplanResult(
                success=False,
                duration_ms=(time.time() - start_time) * 1000,
            )

        # Analyze the failure
        if failed_task.result:
            analysis = self.analyze_failure(failed_task, failed_task.result)
        else:
            analysis = FailureAnalysis(
                failure_type=FailureType.UNKNOWN,
                recovery_strategy=RecoveryStrategy.RETRY,
                confidence=0.5,
                suggestion="No result available for analysis",
            )

        # Create new graph preserving completed tasks
        new_graph = TaskGraph(goal=graph.goal)
        removed_tasks = [failed_task_id]
        added_tasks = []

        # Preserve completed tasks
        for task in graph.tasks:
            if task.status == TaskStatus.DONE:
                new_task = Task(
                    id=task.id,
                    description=task.description,
                    action=task.action,
                    arguments=task.arguments.copy(),
                    dependencies=[],
                    risk_level=task.risk_level,
                    status=TaskStatus.DONE,
                    result=task.result,
                )
                new_graph.add_task(new_task)
                new_graph.mark_task_done(task.id)

        # Add alternative tasks based on recovery strategy
        if analysis.recovery_strategy == RecoveryStrategy.ALTERNATIVE:
            alt_task = Task(
                description=f"Alternative for: {failed_task.description}",
                action=failed_task.action,
                arguments=failed_task.arguments.copy(),
                dependencies=[],
                risk_level=failed_task.risk_level,
            )
            new_graph.add_task(alt_task)
            added_tasks.append(alt_task.id)
        elif analysis.recovery_strategy in (RecoveryStrategy.RETRY, RecoveryStrategy.RETRY_WITH_DELAY):
            retry_task = Task(
                description=failed_task.description,
                action=failed_task.action,
                arguments=failed_task.arguments.copy(),
                dependencies=[],
                risk_level=failed_task.risk_level,
                retry_count=failed_task.retry_count + 1,
                max_retries=failed_task.max_retries,
            )
            new_graph.add_task(retry_task)
            added_tasks.append(retry_task.id)

        self._replan_count += 1

        return ReplanResult(
            success=True,
            graph=new_graph,
            removed_tasks=removed_tasks,
            added_tasks=added_tasks,
            analysis=analysis,
            duration_ms=(time.time() - start_time) * 1000,
        )

    def _recover_network(self, task: Task, retry_count: int) -> FailureAnalysis:
        """Recovery strategy for network failures."""
        if retry_count < 2:
            return FailureAnalysis(
                failure_type=FailureType.NETWORK,
                recovery_strategy=RecoveryStrategy.RETRY_WITH_DELAY,
                confidence=0.8,
                suggestion="Network error — retry after a short delay",
            )
        return FailureAnalysis(
            failure_type=FailureType.NETWORK,
            recovery_strategy=RecoveryStrategy.ASK_USER,
            confidence=0.6,
            suggestion="Network error persists — ask user to check connection",
        )

    def _recover_permission(self, task: Task, retry_count: int) -> FailureAnalysis:
        """Recovery strategy for permission failures."""
        return FailureAnalysis(
            failure_type=FailureType.PERMISSION,
            recovery_strategy=RecoveryStrategy.ASK_USER,
            confidence=0.9,
            suggestion="Permission denied — ask user to grant access",
        )

    def _recover_app(self, task: Task, retry_count: int) -> FailureAnalysis:
        """Recovery strategy for application failures."""
        if retry_count == 0:
            return FailureAnalysis(
                failure_type=FailureType.APP,
                recovery_strategy=RecoveryStrategy.RETRY,
                confidence=0.7,
                suggestion="App not found — retry with different approach",
            )
        return FailureAnalysis(
            failure_type=FailureType.APP,
            recovery_strategy=RecoveryStrategy.ASK_USER,
            confidence=0.6,
            suggestion="App not available — ask user for alternative",
        )

    def _recover_ui(self, task: Task, retry_count: int) -> FailureAnalysis:
        """Recovery strategy for UI element failures."""
        if retry_count < 2:
            return FailureAnalysis(
                failure_type=FailureType.UI,
                recovery_strategy=RecoveryStrategy.RETRY_WITH_DELAY,
                confidence=0.7,
                suggestion="UI element not found — retry after delay",
            )
        return FailureAnalysis(
            failure_type=FailureType.UI,
            recovery_strategy=RecoveryStrategy.ASK_USER,
            confidence=0.5,
            suggestion="UI element consistently missing — ask user for guidance",
        )

    def _recover_timeout(self, task: Task, retry_count: int) -> FailureAnalysis:
        """Recovery strategy for timeout failures."""
        if retry_count < 2:
            return FailureAnalysis(
                failure_type=FailureType.TIMEOUT,
                recovery_strategy=RecoveryStrategy.RETRY_WITH_DELAY,
                confidence=0.75,
                suggestion="Operation timed out — retry with longer timeout",
            )
        return FailureAnalysis(
            failure_type=FailureType.TIMEOUT,
            recovery_strategy=RecoveryStrategy.SKIP,
            confidence=0.5,
            suggestion="Timeout persists — skip and continue",
        )

    def _recover_unknown(self, task: Task, retry_count: int) -> FailureAnalysis:
        """Recovery strategy for unknown failures."""
        if retry_count == 0:
            return FailureAnalysis(
                failure_type=FailureType.UNKNOWN,
                recovery_strategy=RecoveryStrategy.RETRY,
                confidence=0.4,
                suggestion="Unknown error — retry once",
            )
        return FailureAnalysis(
            failure_type=FailureType.UNKNOWN,
            recovery_strategy=RecoveryStrategy.ASK_USER,
            confidence=0.3,
            suggestion="Unknown error persists — ask user for help",
        )

    @property
    def stats(self) -> dict:
        """Return replanner statistics."""
        return {
            "failures_analyzed": self._failure_count,
            "recoveries_determined": self._recovery_count,
            "replans_created": self._replan_count,
        }
