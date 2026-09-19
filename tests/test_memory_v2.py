"""Comprehensive tests for core.memory_v2."""

import json
import time

import pytest

from core.memory_v2 import (
    EpisodicMemory,
    MemoryEntry,
    MemoryRetriever,
    MemoryType,
    Outcome,
    ProceduralMemory,
    Procedure,
    Trajectory,
    TrajectoryStep,
    WorkingMemory,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_step(action="read", success=True, duration_ms=10.0):
    return TrajectoryStep(
        action=action,
        arguments={"file": "test.txt"},
        result="ok",
        success=success,
        duration_ms=duration_ms,
    )


def make_trajectory(goal="test goal", n_steps=3, outcome=Outcome.SUCCESS, tags=None):
    steps = [make_step(action=f"step_{i}", success=(i % 2 == 0)) for i in range(n_steps)]
    return Trajectory(
        goal=goal,
        steps=steps,
        outcome=outcome,
        tags=tags or [],
    )


def make_procedure(name="deploy", success_count=5, failure_count=1):
    return Procedure(
        name=name,
        description=f"Procedure for {name}",
        action_sequence=["build", "test", "deploy"],
        success_count=success_count,
        failure_count=failure_count,
    )


# ---------------------------------------------------------------------------
# 1. TestTrajectory
# ---------------------------------------------------------------------------

class TestTrajectory:
    def test_creation_defaults(self):
        t = Trajectory()
        assert t.id
        assert t.goal == ""
        assert t.steps == []
        assert t.outcome == Outcome.UNKNOWN
        assert t.started_at
        assert t.completed_at is None
        assert t.tags == []
        assert t.metadata == {}

    def test_creation_custom(self):
        t = make_trajectory(goal="build app", n_steps=2, outcome=Outcome.FAILURE, tags=["ci"])
        assert t.goal == "build app"
        assert len(t.steps) == 2
        assert t.outcome == Outcome.FAILURE
        assert t.tags == ["ci"]

    def test_duration_ms_no_completed(self):
        t = Trajectory()
        assert t.duration_ms == 0.0

    def test_duration_ms_valid(self):
        t = Trajectory(started_at="2025-01-01T00:00:00+00:00", completed_at="2025-01-01T00:00:01+00:00")
        assert t.duration_ms == pytest.approx(1000.0)

    def test_duration_ms_invalid_format(self):
        t = Trajectory(started_at="not-a-date", completed_at="also-not-a-date")
        assert t.duration_ms == 0.0

    def test_success_rate_empty(self):
        assert Trajectory().success_rate == 0.0

    def test_success_rate_partial(self):
        steps = [make_step(success=True), make_step(success=False), make_step(success=True)]
        t = Trajectory(steps=steps)
        assert t.success_rate == pytest.approx(2 / 3)

    def test_success_rate_all_success(self):
        steps = [make_step(success=True), make_step(success=True)]
        t = Trajectory(steps=steps)
        assert t.success_rate == 1.0

    def test_to_dict(self):
        t = make_trajectory(goal="x", tags=["a"])
        d = t.to_dict()
        assert d["goal"] == "x"
        assert d["tags"] == ["a"]
        assert d["outcome"] == "success"
        assert isinstance(d["steps"], list)
        assert len(d["steps"]) == 3

    def test_from_dict(self):
        t = make_trajectory(goal="y", n_steps=1)
        d = t.to_dict()
        t2 = Trajectory.from_dict(d)
        assert t2.goal == "y"
        assert len(t2.steps) == 1
        assert t2.outcome == t.outcome

    def test_roundtrip_preserves_all_fields(self):
        t = make_trajectory(goal="roundtrip", n_steps=2, outcome=Outcome.PARTIAL, tags=["t1", "t2"])
        t.metadata["key"] = "value"
        t.completed_at = t.started_at
        d = t.to_dict()
        t2 = Trajectory.from_dict(d)
        assert t2.id == t.id
        assert t2.goal == t.goal
        assert t2.steps[0].action == t.steps[0].action
        assert t2.tags == t.tags
        assert t2.metadata == t.metadata
        assert t2.outcome == t.outcome
        assert t2.started_at == t.started_at
        assert t2.completed_at == t.completed_at

    def test_from_dict_minimal(self):
        t = Trajectory.from_dict({})
        assert t.id
        assert t.goal == ""
        assert t.outcome == Outcome.UNKNOWN


# ---------------------------------------------------------------------------
# 2. TestProcedure
# ---------------------------------------------------------------------------

class TestProcedure:
    def test_creation_defaults(self):
        p = Procedure()
        assert p.id
        assert p.name == ""
        assert p.success_count == 0
        assert p.failure_count == 0

    def test_success_rate_no_uses(self):
        assert Procedure().success_rate == 0.0

    def test_success_rate(self):
        p = make_procedure(success_count=3, failure_count=1)
        assert p.success_rate == pytest.approx(0.75)

    def test_confidence_zero_uses(self):
        assert Procedure().confidence == 0.0

    def test_confidence_few_uses(self):
        p = make_procedure(success_count=3, failure_count=0)
        assert p.confidence == pytest.approx(1.0 * (3 / 10.0))

    def test_confidence_many_uses(self):
        p = make_procedure(success_count=20, failure_count=0)
        assert p.confidence == pytest.approx(1.0 * 1.0)

    def test_confidence_mixed(self):
        p = make_procedure(success_count=7, failure_count=3)
        rate = 7 / 10
        vol = min(10 / 10.0, 1.0)
        assert p.confidence == pytest.approx(rate * vol)

    def test_to_dict(self):
        p = make_procedure()
        d = p.to_dict()
        assert d["name"] == "deploy"
        assert d["success_count"] == 5

    def test_from_dict(self):
        p = make_procedure()
        d = p.to_dict()
        p2 = Procedure.from_dict(d)
        assert p2.name == p.name
        assert p2.success_count == p.success_count

    def test_roundtrip(self):
        p = make_procedure()
        d = p.to_dict()
        p2 = Procedure.from_dict(d)
        assert p2.id == p.id
        assert p2.description == p.description
        assert p2.action_sequence == p.action_sequence
        assert p2.failure_count == p.failure_count

    def test_from_dict_minimal(self):
        p = Procedure.from_dict({})
        assert p.name == ""
        assert p.success_count == 0


# ---------------------------------------------------------------------------
# 3. TestWorkingMemory
# ---------------------------------------------------------------------------

class TestWorkingMemory:
    def test_get_set(self):
        wm = WorkingMemory()
        wm.set("k", 42)
        assert wm.get("k") == 42

    def test_get_default(self):
        wm = WorkingMemory()
        assert wm.get("missing") is None
        assert wm.get("missing", "fallback") == "fallback"

    def test_delete_existing(self):
        wm = WorkingMemory()
        wm.set("k", "v")
        assert wm.delete("k") is True
        assert wm.get("k") is None

    def test_delete_missing(self):
        wm = WorkingMemory()
        assert wm.delete("nonexistent") is False

    def test_clear(self):
        wm = WorkingMemory()
        wm.set("a", 1)
        wm.set("b", 2)
        wm.clear()
        assert wm.size == 0

    def test_keys(self):
        wm = WorkingMemory()
        wm.set("x", 1)
        wm.set("y", 2)
        assert sorted(wm.keys()) == ["x", "y"]

    def test_items(self):
        wm = WorkingMemory()
        wm.set("a", 10)
        items = wm.items()
        assert ("a", 10) in items

    def test_size(self):
        wm = WorkingMemory()
        assert wm.size == 0
        wm.set("k", "v")
        assert wm.size == 1

    def test_age_seconds(self):
        wm = WorkingMemory()
        wm.set("k", "v")
        time.sleep(0.01)
        age = wm.age_seconds("k")
        assert age is not None
        assert age >= 0.01

    def test_age_seconds_missing(self):
        wm = WorkingMemory()
        assert wm.age_seconds("nope") is None


# ---------------------------------------------------------------------------
# 4. TestEpisodicMemory
# ---------------------------------------------------------------------------

class TestEpisodicMemory:
    def _make(self, tmp_path):
        import core.memory_v2 as mod
        episodic_file = tmp_path / "episodic.json"
        mod.EPISODIC_PATH = episodic_file
        return EpisodicMemory()

    def test_record_and_count(self, tmp_path):
        em = self._make(tmp_path)
        t = make_trajectory(goal="g1")
        em.record(t)
        assert em.count == 1

    def test_search_by_goal(self, tmp_path):
        em = self._make(tmp_path)
        em.record(make_trajectory(goal="deploy to prod"))
        em.record(make_trajectory(goal="run tests"))
        results = em.search("deploy")
        assert len(results) == 1
        assert results[0].goal == "deploy to prod"

    def test_search_by_tag(self, tmp_path):
        em = self._make(tmp_path)
        em.record(make_trajectory(goal="g1", tags=["urgent"]))
        results = em.search("urgent")
        assert len(results) == 1

    def test_search_by_step_action(self, tmp_path):
        em = self._make(tmp_path)
        em.record(make_trajectory(goal="g1", n_steps=1))
        em._trajectories[-1].steps[0].action = "compile"
        results = em.search("compile")
        assert len(results) == 1

    def test_search_outcome_filter(self, tmp_path):
        em = self._make(tmp_path)
        em.record(make_trajectory(goal="a", outcome=Outcome.SUCCESS))
        em.record(make_trajectory(goal="b", outcome=Outcome.FAILURE))
        em.record(make_trajectory(goal="c", outcome=Outcome.SUCCESS))
        results = em.search("a", outcome_filter=Outcome.SUCCESS)
        assert all(r.outcome == Outcome.SUCCESS for r in results)

    def test_search_limit(self, tmp_path):
        em = self._make(tmp_path)
        for i in range(5):
            em.record(make_trajectory(goal=f"item {i}"))
        results = em.search("item", limit=2)
        assert len(results) <= 2

    def test_get_successful(self, tmp_path):
        em = self._make(tmp_path)
        em.record(make_trajectory(goal="a", outcome=Outcome.SUCCESS))
        em.record(make_trajectory(goal="b", outcome=Outcome.FAILURE))
        em.record(make_trajectory(goal="c", outcome=Outcome.SUCCESS))
        successful = em.get_successful()
        assert len(successful) == 2
        assert all(t.outcome == Outcome.SUCCESS for t in successful)

    def test_get_recent(self, tmp_path):
        em = self._make(tmp_path)
        em.record(make_trajectory(goal="first"))
        em.record(make_trajectory(goal="second"))
        em.record(make_trajectory(goal="third"))
        recent = em.get_recent(limit=2)
        assert len(recent) == 2
        assert recent[0].goal == "second"
        assert recent[1].goal == "third"

    def test_count(self, tmp_path):
        em = self._make(tmp_path)
        assert em.count == 0
        em.record(make_trajectory(goal="x"))
        assert em.count == 1

    def test_success_rate(self, tmp_path):
        em = self._make(tmp_path)
        em.record(make_trajectory(goal="a", outcome=Outcome.SUCCESS))
        em.record(make_trajectory(goal="b", outcome=Outcome.FAILURE))
        assert em.success_rate == pytest.approx(0.5)

    def test_success_rate_empty(self, tmp_path):
        em = self._make(tmp_path)
        assert em.success_rate == 0.0

    def test_persistence(self, tmp_path):
        em = self._make(tmp_path)
        em.record(make_trajectory(goal="persist"))
        em2 = self._make(tmp_path)
        assert em2.count == 1
        assert em2.get_recent()[0].goal == "persist"


# ---------------------------------------------------------------------------
# 5. TestProceduralMemory
# ---------------------------------------------------------------------------

class TestProceduralMemory:
    def _make(self, tmp_path):
        import core.memory_v2 as mod
        procedural_file = tmp_path / "procedural.json"
        mod.PROCEDURAL_PATH = procedural_file
        return ProceduralMemory()

    def test_record_success_new(self, tmp_path):
        pm = self._make(tmp_path)
        proc = pm.record_success("ship", ["build", "deploy"])
        assert proc.name == "ship"
        assert proc.success_count == 1
        assert proc.action_sequence == ["build", "deploy"]

    def test_record_success_existing(self, tmp_path):
        pm = self._make(tmp_path)
        pm.record_success("ship", ["build"])
        proc = pm.record_success("ship", ["build", "release"])
        assert proc.success_count == 2
        assert proc.action_sequence == ["build", "release"]

    def test_record_failure(self, tmp_path):
        pm = self._make(tmp_path)
        pm.record_success("ship", ["build"])
        pm.record_failure("ship")
        proc = pm.get("ship")
        assert proc.failure_count == 1

    def test_record_failure_nonexistent(self, tmp_path):
        pm = self._make(tmp_path)
        pm.record_failure("ghost")
        assert pm.get("ghost") is None

    def test_get(self, tmp_path):
        pm = self._make(tmp_path)
        pm.record_success("x", ["a"])
        assert pm.get("x") is not None
        assert pm.get("y") is None

    def test_search_name(self, tmp_path):
        pm = self._make(tmp_path)
        pm.record_success("deploy_prod", ["build"])
        pm.record_success("run_tests", ["test"])
        results = pm.search("deploy")
        assert len(results) == 1
        assert results[0].name == "deploy_prod"

    def test_search_description(self, tmp_path):
        pm = self._make(tmp_path)
        pm.record_success("a", ["step"], description="handles database migrations")
        results = pm.search("database")
        assert len(results) == 1

    def test_search_limit(self, tmp_path):
        pm = self._make(tmp_path)
        for i in range(5):
            pm.record_success(f"task_{i}", ["s"])
        results = pm.search("task", limit=3)
        assert len(results) <= 3

    def test_get_confident(self, tmp_path):
        pm = self._make(tmp_path)
        pm.record_success("good", ["s"])
        for _ in range(20):
            pm.record_success("good", ["s"])
        pm.record_success("bad", ["s"])
        pm.record_failure("bad")
        confident = pm.get_confident(min_confidence=0.5)
        assert all(p.confidence >= 0.5 for p in confident)

    def test_count(self, tmp_path):
        pm = self._make(tmp_path)
        assert pm.count == 0
        pm.record_success("a", ["s"])
        assert pm.count == 1
        pm.record_success("b", ["s"])
        assert pm.count == 2

    def test_avg_confidence(self, tmp_path):
        pm = self._make(tmp_path)
        pm.record_success("p1", ["s"])
        for _ in range(20):
            pm.record_success("p1", ["s"])
        pm.record_success("p2", ["s"])
        avg = pm.avg_confidence
        assert avg > 0

    def test_avg_confidence_empty(self, tmp_path):
        pm = self._make(tmp_path)
        assert pm.avg_confidence == 0.0

    def test_persistence(self, tmp_path):
        pm = self._make(tmp_path)
        pm.record_success("persist", ["step"])
        pm2 = self._make(tmp_path)
        assert pm2.count == 1
        assert pm2.get("persist") is not None


# ---------------------------------------------------------------------------
# 6. TestMemoryRetriever
# ---------------------------------------------------------------------------

class TestMemoryRetriever:
    def _make_all(self, tmp_path):
        import core.memory_v2 as mod
        mod.EPISODIC_PATH = tmp_path / "episodic.json"
        mod.PROCEDURAL_PATH = tmp_path / "procedural.json"
        working = WorkingMemory()
        episodic = EpisodicMemory()
        procedural = ProceduralMemory()
        retriever = MemoryRetriever(working, episodic, procedural)
        return working, episodic, procedural, retriever

    def test_retrieve_from_working(self, tmp_path):
        working, _, _, retriever = self._make_all(tmp_path)
        working.set("config", "debug mode")
        results = retriever.retrieve("debug")
        assert len(results) >= 1
        assert any(e.memory_type == MemoryType.WORKING for e in results)

    def test_retrieve_from_episodic(self, tmp_path):
        _, episodic, _, retriever = self._make_all(tmp_path)
        episodic.record(make_trajectory(goal="fix login bug"))
        results = retriever.retrieve("login")
        assert len(results) >= 1
        assert any(e.memory_type == MemoryType.EPISODIC for e in results)

    def test_retrieve_from_procedural(self, tmp_path):
        _, _, procedural, retriever = self._make_all(tmp_path)
        for _ in range(10):
            procedural.record_success("deploy_to_prod", ["build", "ship"], description="production deployment")
        results = retriever.retrieve("production deployment")
        assert len(results) >= 1
        assert any(e.memory_type == MemoryType.PROCEDURAL for e in results)

    def test_retrieve_scoring_order(self, tmp_path):
        working, episodic, _, retriever = self._make_all(tmp_path)
        working.set("exact match deploy", "value")
        episodic.record(make_trajectory(goal="deploy something"))
        results = retriever.retrieve("deploy")
        scores = [e.score for e in results]
        assert scores == sorted(scores, reverse=True)

    def test_retrieve_limit(self, tmp_path):
        working, _, _, retriever = self._make_all(tmp_path)
        for i in range(10):
            working.set(f"key_{i}", f"val_{i} deploy stuff")
        results = retriever.retrieve("deploy", limit=3)
        assert len(results) <= 3

    def test_retrieve_exclude_types(self, tmp_path):
        working, episodic, _, retriever = self._make_all(tmp_path)
        working.set("deploy config", "x")
        episodic.record(make_trajectory(goal="deploy app"))
        results = retriever.retrieve("deploy", include_working=False, include_procedural=False)
        assert all(e.memory_type == MemoryType.EPISODIC for e in results)

    def test_detect_contradictions_is_vs_is_not(self, tmp_path):
        _, _, _, retriever = self._make_all(tmp_path)
        new_fact = "the server is running"
        existing = ["the server is not running"]
        contradictions = retriever.detect_contradictions(new_fact, existing)
        assert "the server is not running" in contradictions

    def test_detect_contradictions_opposite_values(self, tmp_path):
        _, _, _, retriever = self._make_all(tmp_path)
        new_fact = "the server is not running"
        existing = ["the server is running"]
        contradictions = retriever.detect_contradictions(new_fact, existing)
        assert "the server is running" in contradictions

    def test_detect_contradictions_no_overlap(self, tmp_path):
        _, _, _, retriever = self._make_all(tmp_path)
        contradictions = retriever.detect_contradictions("cats are soft", ["dogs are loud"])
        assert contradictions == []

    def test_detect_contradictions_same_polarity(self, tmp_path):
        _, _, _, retriever = self._make_all(tmp_path)
        new_fact = "the server is running"
        existing = ["the server is running"]
        contradictions = retriever.detect_contradictions(new_fact, existing)
        assert contradictions == []

    def test_detect_contradictions_negation_variants(self, tmp_path):
        _, _, _, retriever = self._make_all(tmp_path)
        pairs = [
            ("the cache is enabled", "the cache is not enabled"),
            ("I never deploy on fridays", "I deploy on fridays"),
            ("the server is not healthy", "the server is healthy"),
        ]
        for new_fact, existing_fact in pairs:
            contradictions = retriever.detect_contradictions(new_fact, [existing_fact])
            assert len(contradictions) == 1, f"Expected contradiction for {new_fact} vs {existing_fact}"
