"""
Tests for core/replanner.py — Adaptive replanning when tasks fail.
"""

import pytest
from core.replanner import (
    Replanner, FailureAnalysis, ReplanResult,
    FailureType, RecoveryStrategy,
)
from core.task_graph import Task, TaskGraph, TaskResult, TaskStatus, Risk


@pytest.fixture
def replanner():
    return Replanner()


@pytest.fixture
def sample_task():
    return Task(
        id="task1",
        description="Open notepad",
        action="open_app",
        arguments={"app_name": "notepad"},
    )


@pytest.fixture
def failed_task():
    task = Task(
        id="task1",
        description="Open notepad",
        action="open_app",
        arguments={"app_name": "notepad"},
        status=TaskStatus.FAILED,
        retry_count=0,
        max_retries=3,
    )
    task.result = TaskResult(
        success=False,
        errors=["Connection refused"],
    )
    return task


@pytest.fixture
def sample_graph():
    graph = TaskGraph(goal="Open notepad and type hello")
    t1 = Task(
        id="task1",
        description="Open notepad",
        action="open_app",
        arguments={"app_name": "notepad"},
        status=TaskStatus.DONE,
        result=TaskResult(success=True),
    )
    t2 = Task(
        id="task2",
        description="Type hello",
        action="type_text",
        arguments={"text": "hello"},
        dependencies=["task1"],
        status=TaskStatus.FAILED,
        retry_count=0,
    )
    t2.result = TaskResult(success=False, errors=["Element not found"])
    graph.add_task(t1)
    graph.add_task(t2)
    graph.mark_task_done("task1")
    return graph


class TestReplanner:

    def test_classify_failure_network(self, replanner):
        assert replanner.classify_failure("Connection refused") == FailureType.NETWORK

    def test_classify_failure_permission(self, replanner):
        assert replanner.classify_failure("Permission denied") == FailureType.PERMISSION

    def test_classify_failure_app(self, replanner):
        assert replanner.classify_failure("Application is not installed") == FailureType.APP

    def test_classify_failure_ui(self, replanner):
        assert replanner.classify_failure("Button element not found") == FailureType.UI

    def test_classify_failure_timeout(self, replanner):
        assert replanner.classify_failure("Operation timed out") == FailureType.TIMEOUT

    def test_classify_failure_unknown(self, replanner):
        assert replanner.classify_failure("Something went wrong") == FailureType.UNKNOWN

    def test_determine_recovery_network(self, replanner, sample_task):
        analysis = replanner.determine_recovery(FailureType.NETWORK, sample_task, 0)
        assert analysis.failure_type == FailureType.NETWORK
        assert analysis.recovery_strategy == RecoveryStrategy.RETRY_WITH_DELAY

    def test_determine_recovery_permission(self, replanner, sample_task):
        analysis = replanner.determine_recovery(FailureType.PERMISSION, sample_task, 0)
        assert analysis.failure_type == FailureType.PERMISSION
        assert analysis.recovery_strategy == RecoveryStrategy.ASK_USER

    def test_determine_recovery_app(self, replanner, sample_task):
        analysis = replanner.determine_recovery(FailureType.APP, sample_task, 0)
        assert analysis.failure_type == FailureType.APP
        assert analysis.recovery_strategy == RecoveryStrategy.RETRY

    def test_determine_recovery_ui(self, replanner, sample_task):
        analysis = replanner.determine_recovery(FailureType.UI, sample_task, 0)
        assert analysis.failure_type == FailureType.UI
        assert analysis.recovery_strategy == RecoveryStrategy.RETRY_WITH_DELAY

    def test_determine_recovery_timeout(self, replanner, sample_task):
        analysis = replanner.determine_recovery(FailureType.TIMEOUT, sample_task, 0)
        assert analysis.failure_type == FailureType.TIMEOUT
        assert analysis.recovery_strategy == RecoveryStrategy.RETRY_WITH_DELAY

    def test_determine_recovery_unknown(self, replanner, sample_task):
        analysis = replanner.determine_recovery(FailureType.UNKNOWN, sample_task, 0)
        assert analysis.failure_type == FailureType.UNKNOWN
        assert analysis.recovery_strategy == RecoveryStrategy.RETRY

    def test_analyze_failure_integration(self, replanner, failed_task):
        analysis = replanner.analyze_failure(failed_task, failed_task.result)
        assert analysis.failure_type == FailureType.NETWORK
        assert analysis.original_error == "Connection refused"
        assert analysis.suggestion

    def test_replan_preserves_completed_tasks(self, replanner, sample_graph):
        result = replanner.replan(sample_graph, "task2")
        assert result.success is True
        completed = [t for t in result.graph.tasks if t.status == TaskStatus.DONE]
        assert len(completed) == 1
        assert completed[0].id == "task1"

    def test_replan_removes_failed_task(self, replanner, sample_graph):
        result = replanner.replan(sample_graph, "task2")
        assert "task2" in result.removed_tasks
        assert result.graph.get_task("task2") is None

    def test_replan_adds_new_tasks(self, replanner, sample_graph):
        result = replanner.replan(sample_graph, "task2")
        assert len(result.added_tasks) >= 1

    def test_replan_invalid_task_id(self, replanner, sample_graph):
        result = replanner.replan(sample_graph, "nonexistent")
        assert result.success is False

    def test_replan_returns_duration(self, replanner, sample_graph):
        result = replanner.replan(sample_graph, "task2")
        assert result.duration_ms >= 0

    def test_replan_includes_analysis(self, replanner, sample_graph):
        result = replanner.replan(sample_graph, "task2")
        assert result.analysis is not None
        assert isinstance(result.analysis, FailureAnalysis)

    def test_stats_tracking(self, replanner):
        assert replanner.stats["failures_analyzed"] == 0
        assert replanner.stats["replans_created"] == 0
        task = Task(
            id="t1",
            description="Test",
            status=TaskStatus.FAILED,
            result=TaskResult(success=False, errors=["error"]),
        )
        replanner.analyze_failure(task, task.result)
        assert replanner.stats["failures_analyzed"] == 1

    def test_stats_replan_count(self, replanner, sample_graph):
        replanner.replan(sample_graph, "task2")
        assert replanner.stats["replans_created"] == 1

    def test_recover_network_retry_limit(self, replanner, sample_task):
        analysis = replanner.determine_recovery(FailureType.NETWORK, sample_task, 3)
        assert analysis.recovery_strategy == RecoveryStrategy.ASK_USER

    def test_recover_app_retry_limit(self, replanner, sample_task):
        analysis = replanner.determine_recovery(FailureType.APP, sample_task, 2)
        assert analysis.recovery_strategy == RecoveryStrategy.ASK_USER

    def test_recover_ui_retry_limit(self, replanner, sample_task):
        analysis = replanner.determine_recovery(FailureType.UI, sample_task, 3)
        assert analysis.recovery_strategy == RecoveryStrategy.ASK_USER

    def test_recover_timeout_retry_limit(self, replanner, sample_task):
        analysis = replanner.determine_recovery(FailureType.TIMEOUT, sample_task, 3)
        assert analysis.recovery_strategy == RecoveryStrategy.SKIP

    def test_recover_unknown_retry_limit(self, replanner, sample_task):
        analysis = replanner.determine_recovery(FailureType.UNKNOWN, sample_task, 2)
        assert analysis.recovery_strategy == RecoveryStrategy.ASK_USER

    def test_replan_preserves_goal(self, replanner, sample_graph):
        result = replanner.replan(sample_graph, "task2")
        assert result.graph.goal == sample_graph.goal
