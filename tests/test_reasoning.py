"""
Tests for core/reasoning.py — Reasoning engine, artifacts, and self-checks.
"""

import pytest
from core.reasoning import (
    ReasoningArtifact,
    ReasoningMode,
    ConfidenceLevel,
    CheckResult,
    SelfCheck,
    ReasoningEngine,
)


@pytest.fixture
def engine():
    return ReasoningEngine()


@pytest.fixture
def sample_artifact():
    return ReasoningArtifact(
        task="open notepad",
        assumptions=[],
        evidence=["Context app: notepad"],
        reasoning_chain=[
            "Analyzing task: open notepad",
            "Considering 1 pieces of evidence.",
            "Making 0 assumptions.",
            "Synthesizing information to form a conclusion.",
            "Conclusion: Task requires further specific logic implementation.",
        ],
        conclusion="Task requires further specific logic implementation.",
        confidence=0.7,
        alternatives=[
            "Alternative 1: Use different approach for open notepad",
            "Alternative 2: Verify conclusion with external tools",
        ],
        decision="Decision based on confidence 0.70.",
        risks=["Risk: Assumptions might be incorrect."],
        mode=ReasoningMode.DEEP,
    )


class TestReasoningArtifact:

    def test_creation(self, sample_artifact):
        assert sample_artifact.task == "open notepad"
        assert sample_artifact.confidence == 0.7
        assert sample_artifact.mode == ReasoningMode.DEEP
        assert len(sample_artifact.evidence) == 1

    def test_to_dict(self, sample_artifact):
        d = sample_artifact.to_dict()
        assert d["task"] == "open notepad"
        assert d["mode"] == "DEEP"
        assert isinstance(d["evidence"], list)
        assert "created_at" in d

    def test_from_dict_roundtrip(self, sample_artifact):
        d = sample_artifact.to_dict()
        restored = ReasoningArtifact.from_dict(d)
        assert restored.task == sample_artifact.task
        assert restored.mode == sample_artifact.mode
        assert restored.confidence == sample_artifact.confidence
        assert restored.evidence == sample_artifact.evidence
        assert restored.conclusion == sample_artifact.conclusion

    def test_get_confidence_level_very_low(self, sample_artifact):
        sample_artifact.confidence = 0.1
        assert sample_artifact.get_confidence_level() == ConfidenceLevel.VERY_LOW

    def test_get_confidence_level_low(self, sample_artifact):
        sample_artifact.confidence = 0.3
        assert sample_artifact.get_confidence_level() == ConfidenceLevel.LOW

    def test_get_confidence_level_medium(self, sample_artifact):
        sample_artifact.confidence = 0.5
        assert sample_artifact.get_confidence_level() == ConfidenceLevel.MEDIUM

    def test_get_confidence_level_high(self, sample_artifact):
        sample_artifact.confidence = 0.7
        assert sample_artifact.get_confidence_level() == ConfidenceLevel.HIGH

    def test_get_confidence_level_very_high(self, sample_artifact):
        sample_artifact.confidence = 0.9
        assert sample_artifact.get_confidence_level() == ConfidenceLevel.VERY_HIGH


class TestSelfCheck:

    def test_is_valid(self):
        check = SelfCheck(
            result=CheckResult.VALID,
            contradictions=[],
            missing_evidence=[],
            assumptions_to_verify=[],
            recommendations=[],
        )
        assert check.is_valid is True
        assert check.needs_attention is False

    def test_needs_attention(self):
        check = SelfCheck(
            result=CheckResult.UNCERTAIN,
            contradictions=[],
            missing_evidence=["No evidence provided."],
            assumptions_to_verify=["some assumption"],
            recommendations=["Gather more evidence."],
        )
        assert check.is_valid is False
        assert check.needs_attention is True


class TestReasoningEngine:

    def test_reason_basic_task(self, engine):
        artifact = engine.reason("open notepad")
        assert artifact.task == "open notepad"
        assert isinstance(artifact.evidence, list)
        assert isinstance(artifact.reasoning_chain, list)
        assert len(artifact.reasoning_chain) > 0
        assert 0.0 <= artifact.confidence <= 1.0

    def test_reason_with_evidence(self, engine):
        context = {"app": "notepad", "source": "user_request"}
        artifact = engine.reason("open notepad", context=context)
        assert any("app" in e for e in artifact.evidence)
        assert any("source" in e for e in artifact.evidence)

    def test_assess_confidence_high(self, engine):
        evidence = ["e1", "e2", "e3", "e4"]
        chain = ["s1", "s2", "s3", "s4", "s5"]
        assumptions = ["a1", "a2"]
        confidence = engine._assess_confidence(evidence, chain, assumptions)
        assert confidence >= 0.6

    def test_assess_confidence_low(self, engine):
        evidence = ["e1"]
        chain = ["s1", "s2"]
        assumptions = ["a1", "a2", "a3"]
        confidence = engine._assess_confidence(evidence, chain, assumptions)
        assert confidence == pytest.approx(0.5)

    def test_generate_alternatives(self, engine):
        alternatives = engine._generate_alternatives("open notepad", "some conclusion")
        assert len(alternatives) == 2
        assert any("open notepad" in a for a in alternatives)

    def test_assess_risks(self, engine):
        risks = engine._assess_risks("open notepad", "some conclusion")
        assert len(risks) == 2
        assert any("open notepad" in r for r in risks)

    def test_self_check_valid(self, engine, sample_artifact):
        check = engine.self_check(sample_artifact)
        assert check.result == CheckResult.VALID
        assert check.is_valid is True
        assert len(check.contradictions) == 0

    def test_self_check_with_issues(self, engine):
        artifact = ReasoningArtifact(
            task="test task",
            assumptions=["assumption 1", "assumption 2"],
            evidence=[],
            reasoning_chain=["step 1"],
            conclusion="done",
            confidence=0.3,
            alternatives=[],
            decision="proceed",
            risks=[],
            mode=ReasoningMode.FAST,
        )
        check = engine.self_check(artifact)
        assert check.result != CheckResult.VALID
        assert check.needs_attention is True
        assert len(check.missing_evidence) > 0
        assert len(check.assumptions_to_verify) > 0

    def test_stats_tracking(self, engine):
        assert engine.stats["reasoning_count"] == 0
        engine.reason("task 1")
        assert engine.stats["reasoning_count"] == 1
        engine.reason("task 2")
        assert engine.stats["reasoning_count"] == 2
        assert engine.stats["avg_confidence"] > 0
        assert engine.stats["mode_counts"][ReasoningMode.DEEP] == 2
