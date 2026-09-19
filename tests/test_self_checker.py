"""
Tests for core/self_checker.py — Fact verification and contradiction detection.
"""

import pytest
from core.self_checker import (
    Fact,
    SelfChecker,
    VerificationStatus,
    ContradictionType,
)


@pytest.fixture
def checker():
    return SelfChecker()


class TestFact:

    def test_creation(self):
        fact = Fact(
            statement="天空是蓝色的",
            source="observation",
            confidence=0.9,
            timestamp=1000.0,
        )
        assert fact.statement == "天空是蓝色的"
        assert fact.source == "observation"
        assert fact.confidence == 0.9
        assert fact.verified is False


class TestSelfChecker:

    def test_verify_fact_verified(self, checker):
        fact = Fact(
            statement="天空是蓝色的",
            source="observer1",
            confidence=0.8,
            timestamp=1000.0,
        )
        known = [
            Fact(
                statement="天空是蓝色的",
                source="observer2",
                confidence=0.9,
                timestamp=1000.0,
            )
        ]
        result = checker.verify_fact(fact, known)
        assert result.status == VerificationStatus.VERIFIED
        assert len(result.supporting_evidence) > 0

    def test_verify_fact_unverified(self, checker):
        fact = Fact(
            statement="天空是蓝色的",
            source="observer1",
            confidence=0.8,
            timestamp=1000.0,
        )
        result = checker.verify_fact(fact, [])
        assert result.status == VerificationStatus.UNVERIFIED
        assert result.confidence < fact.confidence

    def test_verify_fact_contradicted(self, checker):
        fact = Fact(
            statement="天空是蓝色的",
            source="observer1",
            confidence=0.8,
            timestamp=1000.0,
        )
        known = [
            Fact(
                statement="天空不是蓝色的",
                source="observer2",
                confidence=0.9,
                timestamp=1000.0,
            )
        ]
        result = checker.verify_fact(fact, known)
        assert result.status == VerificationStatus.CONTRADICTED
        assert len(result.contradicting_evidence) > 0

    def test_detect_contradictions_direct_negation(self, checker):
        facts = [
            Fact(statement="天空是蓝色的", source="s1", confidence=0.9, timestamp=1.0),
            Fact(statement="天空不是蓝色的", source="s2", confidence=0.9, timestamp=2.0),
        ]
        contradictions = checker.detect_contradictions(facts)
        assert len(contradictions) > 0
        assert any(
            c.contradiction_type == ContradictionType.DIRECT_NEGATION
            for c in contradictions
        )

    def test_detect_contradictions_opposite_values(self, checker):
        facts = [
            Fact(statement="温度是高的", source="s1", confidence=0.9, timestamp=1.0),
            Fact(statement="温度是低的", source="s1", confidence=0.9, timestamp=2.0),
        ]
        contradictions = checker.detect_contradictions(facts)
        assert len(contradictions) > 0
        assert any(
            c.contradiction_type == ContradictionType.OPPOSITE_VALUE
            for c in contradictions
        )

    def test_detect_contradictions_no_contradiction(self, checker):
        facts = [
            Fact(statement="天空是蓝色的", source="s1", confidence=0.9, timestamp=1.0),
            Fact(statement="草是绿色的", source="s1", confidence=0.9, timestamp=2.0),
        ]
        contradictions = checker.detect_contradictions(facts)
        assert len(contradictions) == 0

    def test_check_reasoning_consistency_consistent(self, checker):
        premises = ["天空是蓝色的", "太阳在照耀"]
        conclusion = "天空今天是蓝色的"
        consistent, issues = checker.check_reasoning_consistency(premises, conclusion)
        assert consistent is True
        assert len(issues) == 0

    def test_check_reasoning_consistency_inconsistent(self, checker):
        premises = ["天空是蓝色的"]
        conclusion = "天空不是蓝色的"
        consistent, issues = checker.check_reasoning_consistency(premises, conclusion)
        assert consistent is False
        assert len(issues) > 0

    def test_are_opposites(self):
        assert SelfChecker._are_opposites("是", "不是") is True
        assert SelfChecker._are_opposites("有", "没有") is True
        assert SelfChecker._are_opposites("大于", "小于") is True
        assert SelfChecker._are_opposites("蓝", "绿") is False

    def test_extract_subject(self):
        assert SelfChecker._extract_subject("天空是蓝色的") == "天空"
        assert SelfChecker._extract_subject("我有一只猫") == "我"
        assert SelfChecker._extract_subject("") == ""

    def test_extract_predicate(self):
        assert SelfChecker._extract_predicate("天空是蓝色的") == "是蓝色的"
        assert SelfChecker._extract_predicate("我有一只猫") == "有一只猫"
        assert SelfChecker._extract_predicate("hello world") == "hello world"

    def test_stats(self, checker):
        assert checker.stats["checks_performed"] == 0
        assert checker.stats["contradictions_found"] == 0
        fact = Fact(
            statement="天空是蓝色的",
            source="s1",
            confidence=0.8,
            timestamp=1.0,
        )
        checker.verify_fact(fact, [])
        assert checker.stats["checks_performed"] == 1
        facts = [
            Fact(statement="天空是蓝色的", source="s1", confidence=0.9, timestamp=1.0),
            Fact(statement="天空不是蓝色的", source="s2", confidence=0.9, timestamp=2.0),
        ]
        checker.detect_contradictions(facts)
        assert checker.stats["contradictions_found"] > 0
