"""
Tests for core/task_graph.py — Task Graph data structures.
"""

import time
import pytest
from core.task_graph import (
    Task, TaskGraph, TaskResult, TaskStatus, GraphStatus, Risk,
)


@pytest.fixture
def sample_task():
    return Task(
        id="task1",
        description="Test task",
        action="test_action",
        arguments={"key": "value"},
        risk_level=Risk.SAFE,
    )


@pytest.fixture
def task_graph():
    return TaskGraph(goal="Test goal")


class TestTask:
    def test_creation_defaults(self):
        task = Task()
        assert task.id
        assert task.description == ""
        assert task.action is None
        assert task.arguments == {}
        assert task.dependencies == []
        assert task.risk_level == Risk.SAFE
        assert task.status == TaskStatus.PENDING
        assert task.result is None
        assert task.retry_count == 0
        assert task.max_retries == 3

    def test_creation_custom(self, sample_task):
        assert sample_task.id == "task1"
        assert sample_task.description == "Test task"
        assert sample_task.action == "test_action"
        assert sample_task.arguments == {"key": "value"}

    def test_is_terminal_pending(self, sample_task):
        assert sample_task.is_terminal is False

    def test_is_terminal_done(self, sample_task):
        sample_task.status = TaskStatus.DONE
        assert sample_task.is_terminal is True

    def test_is_terminal_failed(self, sample_task):
        sample_task.status = TaskStatus.FAILED
        assert sample_task.is_terminal is True

    def test_is_terminal_skipped(self, sample_task):
        sample_task.status = TaskStatus.SKIPPED
        assert sample_task.is_terminal is True

    def test_is_terminal_running(self, sample_task):
        sample_task.status = TaskStatus.RUNNING
        assert sample_task.is_terminal is False

    def test_can_retry_failed_under_limit(self, sample_task):
        sample_task.status = TaskStatus.FAILED
        sample_task.retry_count = 0
        assert sample_task.can_retry() is True

    def test_can_retry_failed_at_limit(self, sample_task):
        sample_task.status = TaskStatus.FAILED
        sample_task.retry_count = 3
        assert sample_task.can_retry() is False

    def test_can_retry_not_failed(self, sample_task):
        sample_task.status = TaskStatus.PENDING
        assert sample_task.can_retry() is False

    def test_start(self, sample_task):
        sample_task.start()
        assert sample_task.status == TaskStatus.RUNNING
        assert sample_task.started_at is not None
        assert sample_task.started_at <= time.time()

    def test_complete_success(self, sample_task):
        sample_task.start()
        result = TaskResult(success=True, output="done")
        sample_task.complete(result)
        assert sample_task.status == TaskStatus.DONE
        assert sample_task.result == result
        assert sample_task.completed_at is not None

    def test_complete_failure(self, sample_task):
        sample_task.start()
        result = TaskResult(success=False, errors=["bad"])
        sample_task.complete(result)
        assert sample_task.status == TaskStatus.FAILED
        assert sample_task.result.errors == ["bad"]

    def test_skip(self, sample_task):
        sample_task.skip("not needed")
        assert sample_task.status == TaskStatus.SKIPPED
        assert sample_task.result is not None
        assert "not needed" in sample_task.result.errors[0]

    def test_duration_ms_not_started(self, sample_task):
        assert sample_task.duration_ms is None

    def test_duration_ms_after_start(self, sample_task):
        sample_task.start()
        time.sleep(0.01)
        duration = sample_task.duration_ms
        assert duration is not None
        assert duration >= 0

    def test_duration_ms_after_complete(self, sample_task):
        sample_task.start()
        time.sleep(0.01)
        sample_task.complete(TaskResult(success=True))
        duration = sample_task.duration_ms
        assert duration >= 0

    def test_to_dict(self, sample_task):
        d = sample_task.to_dict()
        assert d["id"] == "task1"
        assert d["description"] == "Test task"
        assert d["action"] == "test_action"
        assert d["status"] == "pending"
        assert d["risk_level"] == "safe"

    def test_from_dict(self, sample_task):
        d = sample_task.to_dict()
        restored = Task.from_dict(d)
        assert restored.id == "task1"
        assert restored.description == "Test task"
        assert restored.action == "test_action"
        assert restored.status == TaskStatus.PENDING

    def test_from_dict_defaults(self):
        restored = Task.from_dict({})
        assert restored.id
        assert restored.description == ""
        assert restored.risk_level == Risk.SAFE


class TestTaskGraph:
    def test_add_task(self, task_graph, sample_task):
        task_graph.add_task(sample_task)
        assert len(task_graph.tasks) == 1
        assert task_graph.tasks[0].id == "task1"

    def test_get_task(self, task_graph, sample_task):
        task_graph.add_task(sample_task)
        found = task_graph.get_task("task1")
        assert found is not None
        assert found.id == "task1"

    def test_get_task_not_found(self, task_graph):
        assert task_graph.get_task("nonexistent") is None

    def test_remove_task(self, task_graph, sample_task):
        task_graph.add_task(sample_task)
        assert task_graph.remove_task("task1") is True
        assert len(task_graph.tasks) == 0

    def test_remove_task_not_found(self, task_graph):
        assert task_graph.remove_task("nonexistent") is False

    def test_get_ready_tasks_no_deps(self, task_graph):
        t1 = Task(id="t1", status=TaskStatus.PENDING)
        task_graph.add_task(t1)
        ready = task_graph.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].id == "t1"

    def test_get_ready_tasks_with_met_deps(self, task_graph):
        t1 = Task(id="t1", status=TaskStatus.PENDING)
        t2 = Task(id="t2", dependencies=["t1"], status=TaskStatus.PENDING)
        task_graph.add_task(t1)
        task_graph.add_task(t2)
        task_graph.completed.append("t1")
        ready = task_graph.get_ready_tasks()
        assert len(ready) == 2

    def test_get_ready_tasks_with_unmet_deps(self, task_graph):
        t1 = Task(id="t1", status=TaskStatus.PENDING)
        t2 = Task(id="t2", dependencies=["t1"], status=TaskStatus.PENDING)
        task_graph.add_task(t1)
        task_graph.add_task(t2)
        ready = task_graph.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].id == "t1"

    def test_get_blocked_tasks(self, task_graph):
        t1 = Task(id="t1", dependencies=["t2"], status=TaskStatus.PENDING)
        task_graph.add_task(t1)
        blocked = task_graph.get_blocked_tasks()
        assert len(blocked) == 1
        assert blocked[0].id == "t1"

    def test_get_retryable_tasks(self, task_graph):
        t1 = Task(id="t1", status=TaskStatus.FAILED, retry_count=0, max_retries=3)
        t2 = Task(id="t2", status=TaskStatus.FAILED, retry_count=3, max_retries=3)
        t3 = Task(id="t3", status=TaskStatus.PENDING)
        task_graph.add_task(t1)
        task_graph.add_task(t2)
        task_graph.add_task(t3)
        retryable = task_graph.get_retryable_tasks()
        assert len(retryable) == 1
        assert retryable[0].id == "t1"

    def test_mark_task_done(self, task_graph):
        task_graph.mark_task_done("t1")
        assert "t1" in task_graph.completed

    def test_mark_task_done_idempotent(self, task_graph):
        task_graph.mark_task_done("t1")
        task_graph.mark_task_done("t1")
        assert task_graph.completed.count("t1") == 1

    def test_mark_task_failed(self, task_graph):
        task_graph.mark_task_failed("t1")
        assert "t1" in task_graph.failed

    def test_update_status_all_done(self, task_graph):
        t1 = Task(id="t1", status=TaskStatus.DONE)
        t2 = Task(id="t2", status=TaskStatus.DONE)
        task_graph.add_task(t1)
        task_graph.add_task(t2)
        task_graph.update_status()
        assert task_graph.status == GraphStatus.COMPLETED

    def test_update_status_any_failed(self, task_graph):
        t1 = Task(id="t1", status=TaskStatus.DONE)
        t2 = Task(id="t2", status=TaskStatus.FAILED)
        task_graph.add_task(t1)
        task_graph.add_task(t2)
        task_graph.update_status()
        assert task_graph.status == GraphStatus.FAILED

    def test_update_status_running(self, task_graph):
        t1 = Task(id="t1", status=TaskStatus.RUNNING)
        t2 = Task(id="t2", status=TaskStatus.PENDING)
        task_graph.add_task(t1)
        task_graph.add_task(t2)
        task_graph.update_status()
        assert task_graph.status == GraphStatus.EXECUTING

    def test_progress_empty(self, task_graph):
        assert task_graph.progress == 1.0

    def test_progress_partial(self, task_graph):
        t1 = Task(id="t1", status=TaskStatus.DONE)
        t2 = Task(id="t2", status=TaskStatus.PENDING)
        task_graph.add_task(t1)
        task_graph.add_task(t2)
        assert task_graph.progress == 0.5

    def test_progress_all_done(self, task_graph):
        t1 = Task(id="t1", status=TaskStatus.DONE)
        t2 = Task(id="t2", status=TaskStatus.FAILED)
        task_graph.add_task(t1)
        task_graph.add_task(t2)
        assert task_graph.progress == 1.0

    def test_validate_valid(self, task_graph):
        t1 = Task(id="t1")
        t2 = Task(id="t2", dependencies=["t1"])
        task_graph.add_task(t1)
        task_graph.add_task(t2)
        errors = task_graph.validate()
        assert errors == []

    def test_validate_duplicate_ids(self, task_graph):
        t1 = Task(id="t1")
        t2 = Task(id="t1")
        task_graph.add_task(t1)
        task_graph.add_task(t2)
        errors = task_graph.validate()
        assert any("Duplicate" in e for e in errors)

    def test_validate_missing_deps(self, task_graph):
        t1 = Task(id="t1", dependencies=["nonexistent"])
        task_graph.add_task(t1)
        errors = task_graph.validate()
        assert any("unknown task" in e for e in errors)

    def test_validate_circular_deps(self, task_graph):
        t1 = Task(id="t1", dependencies=["t2"])
        t2 = Task(id="t2", dependencies=["t1"])
        task_graph.add_task(t1)
        task_graph.add_task(t2)
        errors = task_graph.validate()
        assert any("Circular" in e for e in errors)

    def test_to_dict(self, task_graph):
        t1 = Task(id="t1", description="First task")
        task_graph.add_task(t1)
        task_graph.goal = "Test goal"
        d = task_graph.to_dict()
        assert d["goal"] == "Test goal"
        assert len(d["tasks"]) == 1
        assert d["tasks"][0]["id"] == "t1"

    def test_from_dict(self, task_graph):
        t1 = Task(id="t1", description="First task")
        task_graph.add_task(t1)
        task_graph.goal = "Test goal"
        d = task_graph.to_dict()
        restored = TaskGraph.from_dict(d)
        assert restored.goal == "Test goal"
        assert len(restored.tasks) == 1
        assert restored.tasks[0].id == "t1"
