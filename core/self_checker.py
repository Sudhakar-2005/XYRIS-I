"""Self-Checker for AETHER-I.

Performs advanced contradiction detection and fact verification
on reasoning artifacts.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from time import time

logger = logging.getLogger(__name__)


class VerificationStatus(Enum):
    """Status of a fact verification."""

    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    CONTRADICTED = "contradicted"
    UNCERTAIN = "uncertain"


class ContradictionType(Enum):
    """Types of contradictions that can be detected."""

    DIRECT_NEGATION = "直接否定"
    OPPOSITE_VALUE = "opposite_value"
    LOGICAL_INCONSISTENCY = "logical_inconsistency"
    TEMPORAL_CONTRADICTION = "temporal_contradiction"
    SOURCE_CONFLICT = "source_conflict"


@dataclass
class Fact:
    """A single factual statement to be verified."""

    statement: str
    source: str
    confidence: float
    timestamp: float
    verified: bool = False


@dataclass
class Contradiction:
    """Represents a detected contradiction between two facts."""

    fact1: Fact
    fact2: Fact
    contradiction_type: ContradictionType
    explanation: str
    severity: float


@dataclass
class VerificationResult:
    """Result of verifying a single fact against known facts."""

    fact: Fact
    status: VerificationStatus
    supporting_evidence: list[str] = field(default_factory=list)
    contradicting_evidence: list[str] = field(default_factory=list)
    confidence: float = 0.0


class SelfChecker:
    """Performs fact verification, contradiction detection, and reasoning consistency checks."""

    _NEGATION_MARKERS = {"不", "没有", "无", "未", "非", "并非", "绝非", "从不", "从未", "不是"}
    _OPPOSITE_PAIRS: list[tuple[str, str]] = [
        ("是", "不是"),
        ("有", "没有"),
        ("能", "不能"),
        ("会", "不会"),
        ("可能", "不可能"),
        ("真", "假"),
        ("对", "错"),
        ("大", "小"),
        ("多", "少"),
        ("高", "低"),
        ("快", "慢"),
        ("好", "坏"),
        ("热", "冷"),
        ("明", "暗"),
        ("正", "负"),
        ("存在", "不存在"),
        ("增加", "减少"),
        ("上升", "下降"),
        ("有效", "无效"),
        ("成功", "失败"),
    ]

    def __init__(self) -> None:
        """Initialise the Self-Checker with internal counters."""
        self._checks_performed = 0
        self._contradictions_found = 0
        logger.info("SelfChecker initialised")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def verify_fact(
        self, fact: Fact, known_facts: list[Fact]
    ) -> VerificationResult:
        """Verify *fact* against a collection of *known_facts*.

        Returns a :class:`VerificationResult` indicating whether the fact
        is supported, contradicted, or uncertain given the available
        evidence.
        """
        self._checks_performed += 1
        supporting: list[str] = []
        contradicting: list[str] = []

        subject = self._extract_subject(fact.statement)
        predicate = self._extract_predicate(fact.statement)

        for kf in known_facts:
            if kf.source == fact.source and kf.statement == fact.statement:
                continue

            kf_subject = self._extract_subject(kf.statement)
            if kf_subject and subject and kf_subject != subject:
                continue

            if self._direct_contradiction(fact, kf):
                contradicting.append(kf.statement)
            elif predicate and self._extract_predicate(kf.statement) == predicate:
                supporting.append(kf.statement)

        if contradicting:
            confidence = max(0.0, fact.confidence - len(contradicting) * 0.15)
            status = VerificationStatus.CONTRADICTED
            self._contradictions_found += 1
        elif supporting:
            confidence = min(1.0, fact.confidence + len(supporting) * 0.1)
            status = VerificationStatus.VERIFIED
        elif fact.verified:
            confidence = fact.confidence
            status = VerificationStatus.VERIFIED
        else:
            confidence = fact.confidence * 0.5
            status = VerificationStatus.UNVERIFIED

        logger.debug(
            "verify_fact: status=%s confidence=%.2f supports=%d contradicts=%d",
            status.value,
            confidence,
            len(supporting),
            len(contradicting),
        )

        return VerificationResult(
            fact=fact,
            status=status,
            supporting_evidence=supporting,
            contradicting_evidence=contradicting,
            confidence=confidence,
        )

    def detect_contradictions(
        self, facts: list[Fact]
    ) -> list[Contradiction]:
        """Scan *facts* and return all detected contradictions.

        Checks for direct contradictions, opposite values, source
        conflicts and temporal inconsistencies.
        """
        contradictions: list[Contradiction] = []
        n = len(facts)

        for i in range(n):
            for j in range(i + 1, n):
                f1, f2 = facts[i], facts[j]

                if self._direct_contradiction(f1, f2):
                    contradictions.append(
                        Contradiction(
                            fact1=f1,
                            fact2=f2,
                            contradiction_type=ContradictionType.DIRECT_NEGATION,
                            explanation=f"Direct negation: '{f1.statement}' vs '{f2.statement}'",
                            severity=0.9,
                        )
                    )
                elif self._opposite_values(f1, f2):
                    contradictions.append(
                        Contradiction(
                            fact1=f1,
                            fact2=f2,
                            contradiction_type=ContradictionType.OPPOSITE_VALUE,
                            explanation=f"Opposite values: '{f1.statement}' vs '{f2.statement}'",
                            severity=0.7,
                        )
                    )

                if f1.source != f2.source and self._direct_contradiction(f1, f2):
                    contradictions.append(
                        Contradiction(
                            fact1=f1,
                            fact2=f2,
                            contradiction_type=ContradictionType.SOURCE_CONFLICT,
                            explanation=(
                                f"Source conflict ({f1.source} vs {f2.source}): "
                                f"'{f1.statement}' vs '{f2.statement}'"
                            ),
                            severity=0.85,
                        )
                    )

                if self._temporal_check(f1, f2):
                    contradictions.append(
                        Contradiction(
                            fact1=f1,
                            fact2=f2,
                            contradiction_type=ContradictionType.TEMPORAL_CONTRADICTION,
                            explanation=(
                                f"Temporal inconsistency: '{f1.statement}' "
                                f"({f1.timestamp}) vs '{f2.statement}' ({f2.timestamp})"
                            ),
                            severity=0.6,
                        )
                    )

        contradictions.extend(self._logical_inconsistency(facts))

        self._contradictions_found += len(contradictions)
        logger.info("detect_contradictions: found %d contradiction(s)", len(contradictions))
        return contradictions

    def check_reasoning_consistency(
        self, premises: list[str], conclusion: str
    ) -> tuple[bool, list[str]]:
        """Check whether *conclusion* is logically consistent with *premises*.

        Returns a tuple of ``(is_consistent, issues)`` where *issues* is a
        list of human-readable problem descriptions.
        """
        issues: list[str] = []
        conclusion_lower = conclusion.lower()
        premise_text = " ".join(premises).lower()

        negation_re = re.compile(r"(?:不|没有|无|未|非|not|no|never|don't|doesn't|isn't|aren't|wasn't|weren't)")
        conclusion_has_negation = bool(negation_re.search(conclusion_lower))
        premise_has_negation = bool(negation_re.search(premise_text))

        if conclusion_has_negation and not premise_has_negation:
            issues.append(
                "Conclusion introduces negation not present in premises"
            )

        if not conclusion_lower.strip():
            issues.append("Conclusion is empty")

        if not premises:
            issues.append("No premises provided")

        # For Chinese text, use character-level comparison
        # For English text, use word-level comparison
        def tokenize(text: str) -> set[str]:
            tokens = set()
            # Split by spaces for English
            for word in text.split():
                tokens.add(word)
            # Also add individual characters for Chinese
            for char in text:
                if ord(char) > 127:  # Non-ASCII (likely Chinese)
                    tokens.add(char)
            return tokens

        conclusion_words = tokenize(conclusion_lower)
        premise_words = tokenize(premise_text)
        overlap = conclusion_words & premise_words
        if not overlap and conclusion_words and premise_words:
            issues.append("Conclusion shares no vocabulary with premises")

        is_consistent = len(issues) == 0
        logger.debug(
            "check_reasoning_consistency: consistent=%s issues=%d",
            is_consistent,
            len(issues),
        )
        return is_consistent, issues

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _direct_contradiction(self, fact1: Fact, fact2: Fact) -> bool:
        """Return True if *fact1* directly negates *fact2* or vice-versa."""
        s1, s2 = fact1.statement.lower(), fact2.statement.lower()
        if s1 == s2:
            return False

        subject1 = self._extract_subject(s1)
        subject2 = self._extract_subject(s2)
        if subject1 and subject2 and subject1 != subject2:
            return False

        # Check if one statement is the negation of the other
        # Pattern: "X is Y" vs "X is not Y" or "X not is Y"
        for neg in self._NEGATION_MARKERS:
            # Check if negation marker appears in s2 but not s1
            if neg in s2 and neg not in s1:
                # Remove the negation marker from s2 and compare
                s2_without_neg = s2.replace(neg, "", 1)
                if s1 == s2_without_neg:
                    return True
                # Also check if the negated form matches
                if s1 in s2_without_neg or s2_without_neg in s1:
                    return True

            # Check if negation marker appears in s1 but not s2
            if neg in s1 and neg not in s2:
                s1_without_neg = s1.replace(neg, "", 1)
                if s2 == s1_without_neg:
                    return True
                if s2 in s1_without_neg or s1_without_neg in s2:
                    return True

        return False

    def _opposite_values(self, fact1: Fact, fact2: Fact) -> bool:
        """Return True if *fact1* and *fact2* express opposite values on the same attribute."""
        subject1 = self._extract_subject(fact1.statement)
        subject2 = self._extract_subject(fact2.statement)
        if subject1 and subject2 and subject1 != subject2:
            return False

        predicate1 = self._extract_predicate(fact1.statement)
        predicate2 = self._extract_predicate(fact2.statement)
        if predicate1 and predicate2 and predicate1 == predicate2:
            return False

        p1 = fact1.statement.lower()
        p2 = fact2.statement.lower()
        for v1, v2 in self._OPPOSITE_PAIRS:
            if (v1 in p1 and v2 in p2) or (v2 in p1 and v1 in p2):
                return True
        return False

    def _logical_inconsistency(
        self, facts: list[Fact]
    ) -> list[Contradiction]:
        """Detect groups of facts that are logically inconsistent together."""
        contradictions: list[Contradiction] = []

        for i in range(len(facts)):
            for j in range(i + 1, len(facts)):
                f1, f2 = facts[i], facts[j]
                s1 = f1.statement.lower()
                s2 = f2.statement.lower()

                subject1 = self._extract_subject(s1)
                subject2 = self._extract_subject(s2)
                if subject1 and subject2 and subject1 == subject2:
                    pred1 = self._extract_predicate(s1)
                    pred2 = self._extract_predicate(s2)
                    if pred1 and pred2 and pred1 != pred2:
                        if self._are_opposites(pred1, pred2):
                            contradictions.append(
                                Contradiction(
                                    fact1=f1,
                                    fact2=f2,
                                    contradiction_type=ContradictionType.LOGICAL_INCONSISTENCY,
                                    explanation=(
                                        f"Logical inconsistency on subject '{subject1}': "
                                        f"'{pred1}' vs '{pred2}'"
                                    ),
                                    severity=0.75,
                                )
                            )
        return contradictions

    def _temporal_check(self, fact1: Fact, fact2: Fact) -> bool:
        """Return True if two facts about the same subject have a suspicious temporal ordering."""
        subject1 = self._extract_subject(fact1.statement)
        subject2 = self._extract_subject(fact2.statement)
        if not subject1 or not subject2 or subject1 != subject2:
            return False
        if fact1.timestamp == fact2.timestamp:
            return False

        predicate1 = self._extract_predicate(fact1.statement)
        predicate2 = self._extract_predicate(fact2.statement)
        if predicate1 and predicate2 and predicate1 != predicate2:
            earlier, later = (
                (fact1, fact2) if fact1.timestamp < fact2.timestamp else (fact2, fact1)
            )
            if earlier.verified and not later.verified:
                return True
        return False

    @staticmethod
    def _extract_subject(statement: str) -> str:
        """Extract the subject from a simple declarative statement."""
        statement = statement.strip()

        # First, check for negation markers and remove them for subject extraction
        negation_markers = ["不是", "没有", "不会", "不能", "不"]
        for neg in negation_markers:
            if neg in statement:
                # Find the position of the verb after the negation
                verb_seps = ("是", "有", "能", "会", "在")
                for verb in verb_seps:
                    verb_idx = statement.find(verb)
                    neg_idx = statement.find(neg)
                    if verb_idx > 0 and neg_idx > 0 and neg_idx < verb_idx:
                        # Subject is everything before the negation marker
                        return statement[:neg_idx].strip()

        for sep in ("是", "有", "能", "会", "在", "了", "过", "的", "对", "把", "被"):
            idx = statement.find(sep)
            if idx > 0:
                return statement[:idx].strip()

        parts = statement.split()
        if parts:
            return parts[0]
        return ""

    @staticmethod
    def _extract_predicate(statement: str) -> str:
        """Extract the predicate portion from a simple declarative statement."""
        statement = statement.strip()
        for sep in ("是", "有", "能", "会", "在", "了", "过", "的", "对", "把", "被"):
            idx = statement.find(sep)
            if idx >= 0:
                remainder = statement[idx:].strip()
                return remainder
        return statement

    @staticmethod
    def _are_opposites(val1: str, val2: str) -> bool:
        """Check whether two predicate strings are semantically opposite."""
        v1 = val1.lower()
        v2 = val2.lower()
        pairs: list[tuple[str, str]] = [
            ("是", "不是"),
            ("有", "没有"),
            ("能", "不能"),
            ("会", "不会"),
            ("可能", "不可能"),
            ("大于", "小于"),
            ("高于", "低于"),
            ("多于", "少于"),
            ("等于", "不等于"),
            ("包含", "不包含"),
            ("支持", "不支持"),
            ("有效", "无效"),
            ("启用", "禁用"),
            ("开", "关"),
        ]
        for a, b in pairs:
            if (a in v1 and b in v2) or (b in v1 and a in v2):
                return True
        return False

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def stats(self) -> dict[str, int]:
        """Return a snapshot of internal counters."""
        return {
            "checks_performed": self._checks_performed,
            "contradictions_found": self._contradictions_found,
        }
