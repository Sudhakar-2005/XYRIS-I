"""
Planner — Goal decomposition and task graph generation.

Part of AETHER-I Intelligence Architecture V0.1
Phase 1: Runtime Foundation (Week 3-4)

The Planner takes a user goal and decomposes it into a TaskGraph
with ordered tasks, dependencies, risk assessments, and verification strategies.
"""

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from core.task_graph import Task, TaskGraph, TaskResult, TaskStatus, GraphStatus, Risk

logger = logging.getLogger(__name__)


class Complexity(Enum):
    """Task complexity levels."""
    TRIVIAL = "trivial"      # Direct tool call, no planning needed
    SIMPLE = "simple"        # Single task, no dependencies
    MODERATE = "moderate"    # 2-3 tasks, linear chain
    COMPLEX = "complex"      # 4+ tasks, branching dependencies
    AMBIGUOUS = "ambiguous"  # Needs clarification before planning


class PlanStrategy(Enum):
    """How to approach planning."""
    LINEAR = "linear"        # Tasks execute in sequence
    PARALLEL = "parallel"    # Independent tasks run concurrently
    ADAPTIVE = "adaptive"    # Mix based on dependencies


@dataclass
class PlanRequest:
    """A request to plan a goal."""
    goal: str
    context: dict = field(default_factory=dict)
    constraints: list[str] = field(default_factory=list)
    available_tools: list[str] = field(default_factory=list)
    max_tasks: int = 20
    risk_tolerance: Risk = Risk.CAUTION


@dataclass
class PlanResult:
    """Result of planning a goal."""
    success: bool
    graph: Optional[TaskGraph] = None
    complexity: Complexity = Complexity.SIMPLE
    strategy: PlanStrategy = PlanStrategy.LINEAR
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    clarification_needed: Optional[str] = None
    duration_ms: float = 0.0


class Planner:
    """Decomposes goals into executable task graphs.

    The Planner analyzes a goal, determines complexity,
    identifies required tools, and generates a TaskGraph
    with proper dependencies and risk assessments.

    Usage:
        planner = Planner()

        result = planner.plan(PlanRequest(
            goal="Open Notepad and type hello world",
            available_tools=["open_app", "type_text"],
        ))

        if result.success:
            for task in result.graph.tasks:
                print(f"  {task.description} [{task.action}]")
    """

    # Keywords that suggest complexity levels
    TRIVIAL_KEYWORDS = {"what time", "what is", "who is", "how many"}
    SIMPLE_KEYWORDS = {"open", "close", "search", "show", "run", "launch"}
    MODERATE_KEYWORDS = {"create", "write", "send", "download", "install", "setup"}
    COMPLEX_KEYWORDS = {"plan", "organize", "automate", "migrate", "deploy", "build"}
    AMBIGUOUS_KEYWORDS = {"help me", "work on", "fix", "improve", "handle"}

    # Risk keywords
    DANGEROUS_KEYWORDS = {"delete", "remove", "uninstall", "format", "drop", "kill"}
    CAUTION_KEYWORDS = {"send", "submit", "purchase", "buy", "pay", "transfer"}

    def __init__(self):
        self._plan_count = 0
        self._avg_tasks_per_plan = 0.0

    def plan(self, request: PlanRequest) -> PlanResult:
        """Plan a goal into a task graph.

        Args:
            request: PlanRequest with goal, context, and constraints

        Returns:
            PlanResult with TaskGraph or clarification needed
        """
        start_time = time.time()

        # Step 1: Assess complexity
        complexity = self._assess_complexity(request.goal)

        # Step 2: Check if clarification is needed
        if complexity == Complexity.AMBIGUOUS:
            return PlanResult(
                success=False,
                complexity=complexity,
                clarification_needed=self._generate_clarification(request.goal),
                duration_ms=(time.time() - start_time) * 1000,
            )

        # Step 3: Decompose goal into tasks
        tasks = self._decompose(request)

        # Step 4: Determine strategy
        strategy = self._determine_strategy(tasks)

        # Step 5: Build task graph
        graph = self._build_graph(request.goal, tasks, strategy)

        # Step 6: Validate graph
        errors = graph.validate()
        if errors:
            logger.warning("Graph validation errors: %s", errors)

        # Step 7: Assess risks
        self._assess_risks(graph, request)

        # Update stats
        self._plan_count += 1
        n = self._plan_count
        self._avg_tasks_per_plan = (
            (self._avg_tasks_per_plan * (n - 1) + len(tasks)) / n
        )

        return PlanResult(
            success=True,
            graph=graph,
            complexity=complexity,
            strategy=strategy,
            assumptions=self._extract_assumptions(request),
            duration_ms=(time.time() - start_time) * 1000,
        )

    def _assess_complexity(self, goal: str) -> Complexity:
        """Assess the complexity of a goal."""
        goal_lower = goal.lower().strip()

        # Check for ambiguous goals
        if any(kw in goal_lower for kw in self.AMBIGUOUS_KEYWORDS):
            if len(goal_lower.split()) < 5:
                return Complexity.AMBIGUOUS

        # Check for complex goals
        if any(kw in goal_lower for kw in self.COMPLEX_KEYWORDS):
            return Complexity.COMPLEX

        # Check for moderate goals
        if any(kw in goal_lower for kw in self.MODERATE_KEYWORDS):
            return Complexity.MODERATE

        # Check for simple goals
        if any(kw in goal_lower for kw in self.SIMPLE_KEYWORDS):
            return Complexity.SIMPLE

        # Check for trivial goals
        if any(kw in goal_lower for kw in self.TRIVIAL_KEYWORDS):
            return Complexity.TRIVIAL

        # Default based on length
        word_count = len(goal_lower.split())
        if word_count <= 3:
            return Complexity.SIMPLE
        elif word_count <= 6:
            return Complexity.MODERATE
        else:
            return Complexity.COMPLEX

    def _generate_clarification(self, goal: str) -> str:
        """Generate a clarification question for ambiguous goals."""
        return (
            f"I'm not sure what you mean by '{goal}'. "
            "Could you be more specific? For example:\n"
            "- What specific action do you want?\n"
            "- What application or file are you referring to?\n"
            "- What's the desired outcome?"
        )

    def _decompose(self, request: PlanRequest) -> list[dict]:
        """Decompose a goal into a list of task definitions.

        Returns list of dicts with keys:
            description, action, arguments, dependencies, risk_level
        """
        goal = request.goal.lower()
        tasks = []

        # Pattern-based decomposition
        # "Open X and do Y" -> [open X, do Y]
        if " and " in goal:
            parts = goal.split(" and ", 1)
            tasks.extend(self._decompose_single(parts[0].strip(), request))
            tasks.extend(self._decompose_single(parts[1].strip(), request))
            # Add dependency: second depends on first
            if len(tasks) >= 2:
                tasks[1]["dependencies"] = [tasks[0].get("id", "task_0")]
        else:
            tasks.extend(self._decompose_single(goal, request))

        # Ensure we have at least one task
        if not tasks:
            tasks.append({
                "description": request.goal,
                "action": None,
                "arguments": {},
                "dependencies": [],
                "risk_level": Risk.SAFE,
            })

        return tasks

    def _decompose_single(self, text: str, request: PlanRequest) -> list[dict]:
        """Decompose a single action phrase into task(s)."""
        tasks = []
        text_lower = text.lower()

        # Open app pattern
        if text_lower.startswith("open ") or text_lower.startswith("launch "):
            app_name = text[5:].strip() if text_lower.startswith("open ") else text[7:].strip()
            tasks.append({
                "id": f"task_{len(tasks)}",
                "description": f"Open {app_name}",
                "action": "open_app",
                "arguments": {"app_name": app_name},
                "dependencies": [],
                "risk_level": Risk.SAFE,
                "verification_strategy": "app_running",
            })

        # Type text pattern
        elif text_lower.startswith("type ") or text_lower.startswith("write "):
            content = text[5:].strip() if text_lower.startswith("type ") else text[6:].strip()
            tasks.append({
                "id": f"task_{len(tasks)}",
                "description": f"Type: {content[:30]}...",
                "action": "type_text",
                "arguments": {"text": content},
                "dependencies": [],
                "risk_level": Risk.SAFE,
            })

        # Search pattern
        elif text_lower.startswith("search ") or text_lower.startswith("find "):
            query = text[7:].strip() if text_lower.startswith("search ") else text[5:].strip()
            tasks.append({
                "id": f"task_{len(tasks)}",
                "description": f"Search for: {query}",
                "action": "web_search",
                "arguments": {"query": query},
                "dependencies": [],
                "risk_level": Risk.SAFE,
            })

        # Send message pattern
        elif text_lower.startswith("send ") or text_lower.startswith("email "):
            tasks.append({
                "id": f"task_{len(tasks)}",
                "description": f"Send: {text}",
                "action": "send_email",
                "arguments": {"content": text},
                "dependencies": [],
                "risk_level": Risk.CAUTION,
                "confirmation_needed": True,
            })

        # Download pattern
        elif text_lower.startswith("download "):
            url = text[9:].strip()
            tasks.append({
                "id": f"task_{len(tasks)}",
                "description": f"Download: {url}",
                "action": "download",
                "arguments": {"url": url},
                "dependencies": [],
                "risk_level": Risk.CAUTION,
            })

        # Delete/remove pattern (dangerous)
        elif text_lower.startswith("delete ") or text_lower.startswith("remove "):
            target = text[7:].strip() if text_lower.startswith("delete ") else text[7:].strip()
            tasks.append({
                "id": f"task_{len(tasks)}",
                "description": f"Delete: {target}",
                "action": "delete",
                "arguments": {"target": target},
                "dependencies": [],
                "risk_level": Risk.DANGEROUS,
                "confirmation_needed": True,
                "reversible": False,
            })

        # Run command pattern
        elif text_lower.startswith("run ") or text_lower.startswith("execute "):
            command = text[4:].strip() if text_lower.startswith("run ") else text[8:].strip()
            tasks.append({
                "id": f"task_{len(tasks)}",
                "description": f"Run: {command}",
                "action": "run_command",
                "arguments": {"command": command},
                "dependencies": [],
                "risk_level": Risk.CAUTION,
                "confirmation_needed": True,
            })

        # Generic fallback
        else:
            tasks.append({
                "id": f"task_{len(tasks)}",
                "description": text,
                "action": None,
                "arguments": {},
                "dependencies": [],
                "risk_level": Risk.SAFE,
            })

        return tasks

    def _determine_strategy(self, tasks: list[dict]) -> PlanStrategy:
        """Determine execution strategy based on task dependencies."""
        has_dependencies = any(t.get("dependencies") for t in tasks)

        if not has_dependencies and len(tasks) > 1:
            return PlanStrategy.PARALLEL
        elif has_dependencies:
            return PlanStrategy.LINEAR
        else:
            return PlanStrategy.LINEAR

    def _build_graph(
        self, goal: str, tasks: list[dict], strategy: PlanStrategy
    ) -> TaskGraph:
        """Build a TaskGraph from task definitions."""
        graph = TaskGraph(goal=goal)

        for task_def in tasks:
            task = Task(
                id=task_def.get("id", f"task_{len(graph.tasks)}"),
                description=task_def.get("description", ""),
                action=task_def.get("action"),
                arguments=task_def.get("arguments", {}),
                dependencies=task_def.get("dependencies", []),
                risk_level=task_def.get("risk_level", Risk.SAFE),
                confirmation_needed=task_def.get("confirmation_needed", False),
                reversible=task_def.get("reversible", True),
                verification_strategy=task_def.get("verification_strategy", "none"),
            )
            graph.add_task(task)

        return graph

    def _assess_risks(self, graph: TaskGraph, request: PlanRequest) -> None:
        """Assess and adjust risk levels based on request constraints."""
        for task in graph.tasks:
            # Check if risk exceeds tolerance
            if task.risk_level.value > request.risk_tolerance.value:
                task.confirmation_needed = True

            # Mark dangerous tasks
            if task.risk_level == Risk.DANGEROUS:
                task.max_retries = 0  # Don't retry dangerous operations

    def _extract_assumptions(self, request: PlanRequest) -> list[str]:
        """Extract assumptions made during planning."""
        assumptions = []

        if not request.available_tools:
            assumptions.append("All required tools are assumed available")

        if request.context:
            assumptions.append(f"Using context: {list(request.context.keys())}")

        return assumptions

    @property
    def stats(self) -> dict:
        """Return planner statistics."""
        return {
            "plans_created": self._plan_count,
            "avg_tasks_per_plan": round(self._avg_tasks_per_plan, 1),
        }
