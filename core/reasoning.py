import logging
import time
from enum import Enum, auto
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any

logger = logging.getLogger(__name__)

class ReasoningMode(Enum):
    FAST = auto()
    DEEP = auto()
    PLANNING = auto()
    TOOL_SELECTION = auto()
    VERIFICATION = auto()
    ERROR_RECOVERY = auto()

class ConfidenceLevel(Enum):
    VERY_LOW = (0.0, 0.2)
    LOW = (0.2, 0.4)
    MEDIUM = (0.4, 0.6)
    HIGH = (0.6, 0.8)
    VERY_HIGH = (0.8, 1.0)

class CheckResult(Enum):
    VALID = auto()
    INVALID = auto()
    UNCERTAIN = auto()
    CONTRADICTION = auto()

@dataclass
class ReasoningArtifact:
    task: str
    assumptions: List[str]
    evidence: List[str]
    reasoning_chain: List[str]
    conclusion: str
    confidence: float
    alternatives: List[str]
    decision: str
    risks: List[str]
    mode: ReasoningMode
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d['mode'] = self.mode.name
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ReasoningArtifact':
        data['mode'] = ReasoningMode[data['mode']]
        return cls(**data)

    def get_confidence_level(self) -> ConfidenceLevel:
        if self.confidence < 0.2:
            return ConfidenceLevel.VERY_LOW
        elif self.confidence < 0.4:
            return ConfidenceLevel.LOW
        elif self.confidence < 0.6:
            return ConfidenceLevel.MEDIUM
        elif self.confidence < 0.8:
            return ConfidenceLevel.HIGH
        else:
            return ConfidenceLevel.VERY_HIGH

@dataclass
class SelfCheck:
    result: CheckResult
    contradictions: List[str]
    missing_evidence: List[str]
    assumptions_to_verify: List[str]
    recommendations: List[str]

    @property
    def is_valid(self) -> bool:
        return self.result == CheckResult.VALID

    @property
    def needs_attention(self) -> bool:
        return self.result != CheckResult.VALID

class ReasoningEngine:
    def __init__(self, memory_retriever=None):
        self.memory_retriever = memory_retriever
        self._stats = {
            "reasoning_count": 0,
            "avg_confidence": 0.0,
            "mode_counts": {mode: 0 for mode in ReasoningMode}
        }

    def reason(self, task: str, context: Optional[Dict[str, Any]] = None, mode: ReasoningMode = ReasoningMode.DEEP) -> ReasoningArtifact:
        logger.info(f"Starting reasoning for task: {task} in mode {mode.name}")
        if context is None:
            context = {}

        evidence = self._gather_evidence(task, context)
        assumptions = [f"Assumption based on context: {k}" for k in context.keys()]
        reasoning_chain = self._build_reasoning_chain(task, evidence, assumptions)
        conclusion = reasoning_chain[-1] if reasoning_chain else "No conclusion reached."
        confidence = self._assess_confidence(evidence, reasoning_chain, assumptions)
        alternatives = self._generate_alternatives(task, conclusion)
        decision = f"Decision based on confidence {confidence:.2f}."
        risks = self._assess_risks(task, conclusion)

        artifact = ReasoningArtifact(
            task=task,
            assumptions=assumptions,
            evidence=evidence,
            reasoning_chain=reasoning_chain,
            conclusion=conclusion,
            confidence=confidence,
            alternatives=alternatives,
            decision=decision,
            risks=risks,
            mode=mode
        )

        self._update_stats(artifact)
        logger.info(f"Reasoning completed. Confidence: {confidence:.2f}")
        return artifact

    def _gather_evidence(self, task: str, context: Dict[str, Any]) -> List[str]:
        evidence = []
        if self.memory_retriever:
            memory_evidence = self.memory_retriever.get_context(task)
            if memory_evidence:
                evidence.extend(memory_evidence)
        for k, v in context.items():
            evidence.append(f"Context {k}: {v}")
        return evidence

    def _build_reasoning_chain(self, task: str, evidence: List[str], assumptions: List[str]) -> List[str]:
        chain = [
            f"Analyzing task: {task}",
            f"Considering {len(evidence)} pieces of evidence.",
            f"Making {len(assumptions)} assumptions.",
            "Synthesizing information to form a conclusion.",
            f"Conclusion: Task requires further specific logic implementation."
        ]
        return chain

    def _assess_confidence(self, evidence: List[str], reasoning_chain: List[str], assumptions: List[str]) -> float:
        base_confidence = 0.5
        if len(evidence) > 3: base_confidence += 0.1
        if len(assumptions) < 3: base_confidence += 0.1
        if len(reasoning_chain) > 4: base_confidence += 0.1
        return min(base_confidence, 1.0)

    def _generate_alternatives(self, task: str, conclusion: str) -> List[str]:
        return [f"Alternative 1: Use different approach for {task}", f"Alternative 2: Verify conclusion {conclusion} with external tools"]

    def _assess_risks(self, task: str, conclusion: str) -> List[str]:
        return [f"Risk: Assumptions for {task} might be incorrect.", "Risk: Missing critical context."]

    def self_check(self, artifact: ReasoningArtifact) -> SelfCheck:
        contradictions = self._check_contradictions(artifact)
        missing_evidence = self._check_evidence_gaps(artifact)
        assumptions_to_verify = self._check_assumptions(artifact)

        result = CheckResult.VALID
        if contradictions:
            result = CheckResult.CONTRADICTION
        elif missing_evidence or assumptions_to_verify:
            result = CheckResult.UNCERTAIN

        recommendations = []
        if contradictions: recommendations.append("Resolve contradictions.")
        if missing_evidence: recommendations.append("Gather more evidence.")
        if assumptions_to_verify: recommendations.append("Verify assumptions.")

        return SelfCheck(
            result=result,
            contradictions=contradictions,
            missing_evidence=missing_evidence,
            assumptions_to_verify=assumptions_to_verify,
            recommendations=recommendations
        )

    def _check_contradictions(self, artifact: ReasoningArtifact) -> List[str]:
        return []

    def _check_evidence_gaps(self, artifact: ReasoningArtifact) -> List[str]:
        if not artifact.evidence:
            return ["No evidence provided."]
        return []

    def _check_assumptions(self, artifact: ReasoningArtifact) -> List[str]:
        return artifact.assumptions[:]

    @property
    def stats(self) -> Dict[str, Any]:
        return self._stats

    def _update_stats(self, artifact: ReasoningArtifact):
        self._stats["reasoning_count"] += 1
        self._stats["mode_counts"][artifact.mode] += 1
        total_conf = self._stats["avg_confidence"] * (self._stats["reasoning_count"] - 1)
        self._stats["avg_confidence"] = (total_conf + artifact.confidence) / self._stats["reasoning_count"]
