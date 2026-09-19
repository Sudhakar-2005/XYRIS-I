"""Memory 2.0 system for AETHER-I.

Provides enhanced memory capabilities beyond the existing memory_manager.py,
including episodic memory (trajectories), procedural memory (action sequences),
and a working memory buffer for session-scoped storage.
"""

import json
import logging
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = _get_base_dir()
MEMORY_V2_DIR = BASE_DIR / "memory" / "v2"
EPISODIC_PATH = MEMORY_V2_DIR / "episodic.json"
PROCEDURAL_PATH = MEMORY_V2_DIR / "procedural.json"


class MemoryType(Enum):
    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"


class Outcome(Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


@dataclass
class TrajectoryStep:
    action: str
    arguments: dict[str, Any]
    result: Any
    success: bool
    duration_ms: float
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "arguments": self.arguments,
            "result": self.result,
            "success": self.success,
            "duration_ms": self.duration_ms,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TrajectoryStep":
        return cls(
            action=data["action"],
            arguments=data.get("arguments", {}),
            result=data.get("result"),
            success=data.get("success", False),
            duration_ms=data.get("duration_ms", 0.0),
            timestamp=data.get("timestamp", datetime.now(timezone.utc).isoformat()),
        )


@dataclass
class Trajectory:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    goal: str = ""
    steps: list[TrajectoryStep] = field(default_factory=list)
    outcome: Outcome = Outcome.UNKNOWN
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        if not self.started_at or not self.completed_at:
            return 0.0
        try:
            start = datetime.fromisoformat(self.started_at)
            end = datetime.fromisoformat(self.completed_at)
            return (end - start).total_seconds() * 1000.0
        except (ValueError, TypeError):
            return 0.0

    @property
    def success_rate(self) -> float:
        if not self.steps:
            return 0.0
        return sum(1 for s in self.steps if s.success) / len(self.steps)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "goal": self.goal,
            "steps": [s.to_dict() for s in self.steps],
            "outcome": self.outcome.value,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "tags": self.tags,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Trajectory":
        return cls(
            id=data.get("id", uuid.uuid4().hex[:12]),
            goal=data.get("goal", ""),
            steps=[TrajectoryStep.from_dict(s) for s in data.get("steps", [])],
            outcome=Outcome(data.get("outcome", "unknown")),
            started_at=data.get("started_at", datetime.now(timezone.utc).isoformat()),
            completed_at=data.get("completed_at"),
            tags=data.get("tags", []),
            metadata=data.get("metadata", {}),
        )


@dataclass
class Procedure:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = ""
    description: str = ""
    action_sequence: list[str] = field(default_factory=list)
    success_count: int = 0
    failure_count: int = 0
    avg_duration_ms: float = 0.0
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_used: Optional[str] = None
    tags: list[str] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        total = self.success_count + self.failure_count
        if total == 0:
            return 0.0
        return self.success_count / total

    @property
    def confidence(self) -> float:
        total = self.success_count + self.failure_count
        if total == 0:
            return 0.0
        rate = self.success_rate
        volume_factor = min(total / 10.0, 1.0)
        return rate * volume_factor

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "action_sequence": self.action_sequence,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "avg_duration_ms": self.avg_duration_ms,
            "created_at": self.created_at,
            "last_used": self.last_used,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Procedure":
        return cls(
            id=data.get("id", uuid.uuid4().hex[:12]),
            name=data.get("name", ""),
            description=data.get("description", ""),
            action_sequence=data.get("action_sequence", []),
            success_count=data.get("success_count", 0),
            failure_count=data.get("failure_count", 0),
            avg_duration_ms=data.get("avg_duration_ms", 0.0),
            created_at=data.get("created_at", datetime.now(timezone.utc).isoformat()),
            last_used=data.get("last_used"),
            tags=data.get("tags", []),
        )


@dataclass
class MemoryEntry:
    memory_type: MemoryType
    content: Any
    relevance: float = 0.0
    recency: float = 0.0
    importance: float = 0.0
    score: float = 0.0


class WorkingMemory:
    """Ephemeral session-scoped storage for transient data."""

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}
        self._timestamps: dict[str, float] = {}
        self._lock = Lock()

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._store.get(key, default)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._store[key] = value
            self._timestamps[key] = time.time()

    def delete(self, key: str) -> bool:
        with self._lock:
            if key in self._store:
                del self._store[key]
                self._timestamps.pop(key, None)
                return True
            return False

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self._timestamps.clear()

    def keys(self) -> list[str]:
        with self._lock:
            return list(self._store.keys())

    def items(self) -> list[tuple[str, Any]]:
        with self._lock:
            return list(self._store.items())

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._store)

    def age_seconds(self, key: str) -> Optional[float]:
        with self._lock:
            ts = self._timestamps.get(key)
            if ts is None:
                return None
            return time.time() - ts


class EpisodicMemory:
    """Persistent trajectory storage with JSON file backing."""

    def __init__(self) -> None:
        self._trajectories: list[Trajectory] = []
        self._lock = Lock()
        self._load()

    def _load(self) -> None:
        if not EPISODIC_PATH.exists():
            self._trajectories = []
            return
        try:
            data = json.loads(EPISODIC_PATH.read_text(encoding="utf-8"))
            self._trajectories = [Trajectory.from_dict(t) for t in data]
        except Exception as e:
            logger.error("Failed to load episodic memory: %s", e)
            self._trajectories = []

    def _save(self) -> None:
        try:
            EPISODIC_PATH.parent.mkdir(parents=True, exist_ok=True)
            EPISODIC_PATH.write_text(
                json.dumps([t.to_dict() for t in self._trajectories], indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error("Failed to save episodic memory: %s", e)

    def record(self, trajectory: Trajectory) -> None:
        with self._lock:
            self._trajectories.append(trajectory)
            self._save()
        logger.info("Recorded trajectory %s: %s", trajectory.id, trajectory.goal)

    def search(self, query: str, limit: int = 10, outcome_filter: Optional[Outcome] = None) -> list[Trajectory]:
        query_lower = query.lower()
        with self._lock:
            results = []
            for t in reversed(self._trajectories):
                if outcome_filter and t.outcome != outcome_filter:
                    continue
                if self._matches_trajectory(t, query_lower):
                    results.append(t)
                    if len(results) >= limit:
                        break
            return results

    def _matches_trajectory(self, trajectory: Trajectory, query_lower: str) -> bool:
        if query_lower in (trajectory.goal or "").lower():
            return True
        for tag in trajectory.tags:
            if query_lower in tag.lower():
                return True
        for step in trajectory.steps:
            if query_lower in (step.action or "").lower():
                return True
        return False

    def get_successful(self, limit: int = 10) -> list[Trajectory]:
        with self._lock:
            successful = [t for t in self._trajectories if t.outcome == Outcome.SUCCESS]
            return successful[-limit:]

    def get_recent(self, limit: int = 10) -> list[Trajectory]:
        with self._lock:
            return list(self._trajectories[-limit:])

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._trajectories)

    @property
    def success_rate(self) -> float:
        with self._lock:
            if not self._trajectories:
                return 0.0
            successful = sum(1 for t in self._trajectories if t.outcome == Outcome.SUCCESS)
            return successful / len(self._trajectories)


class ProceduralMemory:
    """Persistent procedure storage with JSON file backing."""

    def __init__(self) -> None:
        self._procedures: dict[str, Procedure] = {}
        self._lock = Lock()
        self._load()

    def _load(self) -> None:
        if not PROCEDURAL_PATH.exists():
            self._procedures = {}
            return
        try:
            data = json.loads(PROCEDURAL_PATH.read_text(encoding="utf-8"))
            self._procedures = {p["name"]: Procedure.from_dict(p) for p in data if "name" in p}
        except Exception as e:
            logger.error("Failed to load procedural memory: %s", e)
            self._procedures = {}

    def _save(self) -> None:
        try:
            PROCEDURAL_PATH.parent.mkdir(parents=True, exist_ok=True)
            PROCEDURAL_PATH.write_text(
                json.dumps([p.to_dict() for p in self._procedures.values()], indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error("Failed to save procedural memory: %s", e)

    def record_success(self, name: str, action_sequence: list[str], description: str = "", tags: list[str] | None = None) -> Procedure:
        with self._lock:
            if name in self._procedures:
                proc = self._procedures[name]
                proc.success_count += 1
                proc.last_used = datetime.now(timezone.utc).isoformat()
                if action_sequence:
                    proc.action_sequence = action_sequence
                if description:
                    proc.description = description
            else:
                proc = Procedure(
                    name=name,
                    description=description,
                    action_sequence=action_sequence,
                    success_count=1,
                    tags=tags or [],
                    last_used=datetime.now(timezone.utc).isoformat(),
                )
                self._procedures[name] = proc
            self._save()
        return proc

    def record_failure(self, name: str) -> None:
        with self._lock:
            if name in self._procedures:
                self._procedures[name].failure_count += 1
                self._procedures[name].last_used = datetime.now(timezone.utc).isoformat()
                self._save()

    def get(self, name: str) -> Optional[Procedure]:
        with self._lock:
            return self._procedures.get(name)

    def search(self, query: str, limit: int = 10) -> list[Procedure]:
        query_lower = query.lower()
        with self._lock:
            results = []
            for proc in self._procedures.values():
                if query_lower in (proc.name or "").lower() or query_lower in (proc.description or "").lower():
                    results.append(proc)
                    if len(results) >= limit:
                        break
            return results

    def get_confident(self, min_confidence: float = 0.5) -> list[Procedure]:
        with self._lock:
            return [p for p in self._procedures.values() if p.confidence >= min_confidence]

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._procedures)

    @property
    def avg_confidence(self) -> float:
        with self._lock:
            if not self._procedures:
                return 0.0
            return sum(p.confidence for p in self._procedures.values()) / len(self._procedures)


class MemoryRetriever:
    """Retrieves and scores memories from all memory subsystems."""

    def __init__(self, working: WorkingMemory, episodic: EpisodicMemory, procedural: ProceduralMemory) -> None:
        self._working = working
        self._episodic = episodic
        self._procedural = procedural

    def retrieve(
        self,
        query: str,
        include_working: bool = True,
        include_episodic: bool = True,
        include_procedural: bool = True,
        limit: int = 10,
    ) -> list[MemoryEntry]:
        results: list[MemoryEntry] = []

        if include_working:
            for key, value in self._working.items():
                score = self._compute_score(query, key, str(value))
                if score > 0:
                    entry = MemoryEntry(
                        memory_type=MemoryType.WORKING,
                        content={"key": key, "value": value},
                        score=score,
                    )
                    results.append(entry)

        if include_episodic:
            for trajectory in self._episodic.get_recent(limit * 2):
                text = f"{trajectory.goal} {' '.join(s.action for s in trajectory.steps)}"
                score = self._compute_score(query, text, "")
                if score > 0:
                    recency = self._compute_recency(trajectory.started_at)
                    entry = MemoryEntry(
                        memory_type=MemoryType.EPISODIC,
                        content=trajectory,
                        recency=recency,
                        score=score * recency,
                    )
                    results.append(entry)

        if include_procedural:
            for proc in self._procedural.get_confident(0.0):
                text = f"{proc.name} {proc.description} {' '.join(proc.action_sequence)}"
                score = self._compute_score(query, text, "")
                if score > 0:
                    entry = MemoryEntry(
                        memory_type=MemoryType.PROCEDURAL,
                        content=proc,
                        importance=proc.confidence,
                        score=score * proc.confidence,
                    )
                    results.append(entry)

        results.sort(key=lambda e: e.score, reverse=True)
        return results[:limit]

    def detect_contradictions(self, new_fact: str, existing_facts: list[str]) -> list[str]:
        contradictions: list[str] = []
        new_words = set(new_fact.lower().split())
        for fact in existing_facts:
            fact_words = set(fact.lower().split())
            if not new_words.intersection(fact_words):
                continue
            negation_new = any(w in new_fact.lower() for w in ["not", "never", "no", "isn't", "aren't", "wasn't", "won't", "don't", "doesn't", "didn't"])
            negation_fact = any(w in fact.lower() for w in ["not", "never", "no", "isn't", "aren't", "wasn't", "won't", "don't", "doesn't", "didn't"])
            if negation_new != negation_fact and self._same_subject(new_fact, fact):
                contradictions.append(fact)
        return contradictions

    def _matches_query(self, text: str, query: str) -> float:
        text_lower = text.lower()
        query_lower = query.lower()
        if query_lower in text_lower:
            return 1.0
        query_words = query_lower.split()
        if not query_words:
            return 0.0
        matches = sum(1 for w in query_words if w in text_lower)
        return matches / len(query_words)

    def _compute_relevance(self, text: str, query: str) -> float:
        return self._matches_query(text, query)

    def _compute_recency(self, timestamp_str: str) -> float:
        try:
            dt = datetime.fromisoformat(timestamp_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
            return max(0.0, 1.0 / (1.0 + age_hours / 24.0))
        except (ValueError, TypeError):
            return 0.5

    def _compute_score(self, query: str, text: str, extra: str) -> float:
        relevance = self._compute_relevance(f"{text} {extra}", query)
        return relevance

    def _same_subject(self, fact_a: str, fact_b: str) -> bool:
        words_a = set(fact_a.lower().split())
        words_b = set(fact_b.lower().split())
        stop_words = {"i", "am", "is", "are", "was", "were", "the", "a", "an", "my", "your", "his", "her", "its", "our", "they", "we", "you", "he", "she", "it", "that", "this"}
        meaningful_a = words_a - stop_words
        meaningful_b = words_b - stop_words
        if not meaningful_a or not meaningful_b:
            return False
        overlap = len(meaningful_a.intersection(meaningful_b))
        return overlap >= min(2, min(len(meaningful_a), len(meaningful_b)))

    def _estimate_tokens(self, text: str) -> int:
        return len(text.split()) * 4 // 3
