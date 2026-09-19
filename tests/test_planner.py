"""
Tests for core/planner.py — Goal decomposition and task graph generation.
"""

import pytest
from core.planner import (
    Planner, PlanRequest, PlanResult,
    Complexity, PlanStrategy,
)
from core.task_graph import Task, TaskGraph, TaskResult, TaskStatus, Risk


@pytest.fixture
def planner():
    return Planner()


@pytest.fixture
def simple_request():
    return PlanRequest(
        goal="open notepad",
        available_tools=["open_app"],
    )


@pytest.fixture
def compound_request():
    return PlanRequest(
        goal="open notepad and type hello world",
        available_tools=["open_app", "type_text"],
    )


@pytest.fixture
def dangerous_request():
    return PlanRequest(
        goal="delete my files",
        available_tools=["delete"],
    )


@pytest.fixture
def ambiguous_request():
    return PlanRequest(
        goal="help me",
        available_tools=[],
    )


class TestPlanner:

    def test_assess_complexity_trivial(self, planner):
        assert planner._assess_complexity("what time is it") == Complexity.TRIVIAL

    def test_assess_complexity_simple(self, planner):
        assert planner._assess_complexity("open notepad") == Complexity.SIMPLE

    def test_assess_complexity_moderate(self, planner):
        assert planner._assess_complexity("create a new document and save it") == Complexity.MODERATE

    def test_assess_complexity_complex(self, planner):
        assert planner._assess_complexity("plan the deployment of the application") == Complexity.COMPLEX

    def test_assess_complexity_ambiguous(self, planner):
        assert planner._assess_complexity("help me") == Complexity.AMBIGUOUS

    def test_plan_simple_goal(self, planner, simple_request):
        result = planner.plan(simple_request)
        assert result.success is True
        assert result.graph is not None
        assert len(result.graph.tasks) >= 1
        assert result.graph.tasks[0].action == "open_app"
        assert result.complexity == Complexity.SIMPLE

    def test_plan_compound_goal(self, planner, compound_request):
        result = planner.plan(compound_request)
        assert result.success is True
        assert len(result.graph.tasks) == 2
        assert result.graph.tasks[0].action == "open_app"
        assert result.graph.tasks[1].action == "type_text"
        assert result.graph.tasks[1].dependencies == [result.graph.tasks[0].id]

    def test_plan_dangerous_action(self, planner, dangerous_request):
        result = planner.plan(dangerous_request)
        assert result.success is True
        task = result.graph.tasks[0]
        assert task.risk_level == Risk.DANGEROUS
        assert task.confirmation_needed is True
        assert task.reversible is False

    def test_plan_ambiguous_goal(self, planner, ambiguous_request):
        result = planner.plan(ambiguous_request)
        assert result.success is False
        assert result.clarification_needed is not None
        assert "help me" in result.clarification_needed

    def test_decompose_open_pattern(self, planner):
        request = PlanRequest(goal="open notepad", available_tools=["open_app"])
        tasks = planner._decompose(request)
        assert len(tasks) == 1
        assert tasks[0]["action"] == "open_app"
        assert tasks[0]["arguments"]["app_name"] == "notepad"

    def test_decompose_type_pattern(self, planner):
        request = PlanRequest(goal="type hello world", available_tools=["type_text"])
        tasks = planner._decompose(request)
        assert len(tasks) == 1
        assert tasks[0]["action"] == "type_text"
        assert tasks[0]["arguments"]["text"] == "hello world"

    def test_decompose_search_pattern(self, planner):
        request = PlanRequest(goal="search python docs", available_tools=["web_search"])
        tasks = planner._decompose(request)
        assert len(tasks) == 1
        assert tasks[0]["action"] == "web_search"
        assert tasks[0]["arguments"]["query"] == "python docs"

    def test_decompose_send_pattern(self, planner):
        request = PlanRequest(goal="send email to bob", available_tools=["send_email"])
        tasks = planner._decompose(request)
        assert len(tasks) == 1
        assert tasks[0]["action"] == "send_email"
        assert tasks[0]["risk_level"] == Risk.CAUTION

    def test_decompose_download_pattern(self, planner):
        request = PlanRequest(goal="download https://example.com/file.zip", available_tools=["download"])
        tasks = planner._decompose(request)
        assert len(tasks) == 1
        assert tasks[0]["action"] == "download"

    def test_decompose_run_pattern(self, planner):
        request = PlanRequest(goal="run dir", available_tools=["run_command"])
        tasks = planner._decompose(request)
        assert len(tasks) == 1
        assert tasks[0]["action"] == "run_command"
        assert tasks[0]["arguments"]["command"] == "dir"

    def test_decompose_delete_pattern(self, planner):
        request = PlanRequest(goal="delete temp files", available_tools=["delete"])
        tasks = planner._decompose(request)
        assert len(tasks) == 1
        assert tasks[0]["action"] == "delete"
        assert tasks[0]["risk_level"] == Risk.DANGEROUS

    def test_determine_strategy_linear(self, planner):
        tasks = [{"dependencies": ["t0"]}]
        assert planner._determine_strategy(tasks) == PlanStrategy.LINEAR

    def test_determine_strategy_parallel(self, planner):
        tasks = [{"dependencies": []}, {"dependencies": []}]
        assert planner._determine_strategy(tasks) == PlanStrategy.PARALLEL

    def test_determine_strategy_single(self, planner):
        tasks = [{"dependencies": []}]
        assert planner._determine_strategy(tasks) == PlanStrategy.LINEAR

    def test_graph_validation_valid(self, planner, simple_request):
        result = planner.plan(simple_request)
        errors = result.graph.validate()
        assert errors == []

    def test_graph_validation_dependencies(self, planner, compound_request):
        result = planner.plan(compound_request)
        assert len(result.graph.tasks) == 2
        second_task = result.graph.tasks[1]
        assert second_task.dependencies == [result.graph.tasks[0].id]

    def test_risk_assessment_dangerous(self, planner, dangerous_request):
        result = planner.plan(dangerous_request)
        task = result.graph.tasks[0]
        assert task.max_retries == 0

    def test_assumptions_no_tools(self, planner):
        request = PlanRequest(goal="open notepad", available_tools=[])
        assumptions = planner._extract_assumptions(request)
        assert any("tools" in a.lower() for a in assumptions)

    def test_assumptions_with_context(self, planner):
        request = PlanRequest(goal="open notepad", context={"app": "notepad"})
        assumptions = planner._extract_assumptions(request)
        assert any("context" in a.lower() for a in assumptions)

    def test_stats_tracking(self, planner):
        assert planner.stats["plans_created"] == 0
        request = PlanRequest(goal="open notepad", available_tools=["open_app"])
        planner.plan(request)
        assert planner.stats["plans_created"] == 1
        assert planner.stats["avg_tasks_per_plan"] > 0

    def test_stats_multiple_plans(self, planner):
        req1 = PlanRequest(goal="open notepad", available_tools=["open_app"])
        req2 = PlanRequest(goal="open notepad and type hello", available_tools=["open_app", "type_text"])
        planner.plan(req1)
        planner.plan(req2)
        assert planner.stats["plans_created"] == 2

    def test_plan_returns_duration(self, planner, simple_request):
        result = planner.plan(simple_request)
        assert result.duration_ms >= 0

    def test_plan_warnings_on_validation(self, planner):
        request = PlanRequest(goal="open notepad", available_tools=["open_app"])
        result = planner.plan(request)
        assert isinstance(result.warnings, list)
