"""
Event Bus — Async message queue with priority support.

Part of AETHER-I Intelligence Architecture V0.1
Phase 1: Runtime Foundation
"""

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import IntEnum, Enum
from typing import Any, Callable, Coroutine, Optional

logger = logging.getLogger(__name__)


class Priority(IntEnum):
    """Event priority levels (lower = higher priority)."""
    CRITICAL = 0    # Security events, errors requiring immediate attention
    HIGH = 10       # User input, tool results
    NORMAL = 20     # Background tasks, memory operations
    LOW = 30        # Logging, analytics, non-essential updates
    BACKGROUND = 40 # Cleanup, maintenance


class EventType(str, Enum):
    """Built-in event types."""
    # User
    USER_INPUT = "user_input"
    USER_INTERRUPT = "user_interrupt"

    # Model
    MODEL_RESPONSE = "model_response"
    MODEL_ERROR = "model_error"
    MODEL_TOOL_CALL = "model_tool_call"

    # Tool
    TOOL_RESULT = "tool_result"
    TOOL_ERROR = "tool_error"
    TOOL_REGISTERED = "tool_registered"
    TOOL_UNREGISTERED = "tool_unregistered"

    # Memory
    MEMORY_STORED = "memory_stored"
    MEMORY_RETRIEVED = "memory_retrieved"
    MEMORY_CONTRADICTION = "memory_contradiction"

    # Perception
    SCREENSHOT_CAPTURED = "screenshot_captured"
    PERCEPTION_UPDATE = "perception_update"

    # Verification
    VERIFICATION_SUCCESS = "verification_success"
    VERIFICATION_FAILURE = "verification_failure"

    # System
    SYSTEM_STARTUP = "system_startup"
    SYSTEM_SHUTDOWN = "system_shutdown"
    SYSTEM_ERROR = "system_error"
    HEALTH_CHECK = "health_check"

    # Custom
    CUSTOM = "custom"


@dataclass(order=True)
class Event:
    """An event in the system.

    Events are ordered by priority (lower = higher priority),
    then by timestamp (earlier = higher priority).
    """
    priority: Priority
    timestamp: float = field(default_factory=time.time)
    event_type: EventType = EventType.CUSTOM
    source: str = ""
    data: dict = field(default_factory=dict)
    event_id: str = ""
    correlation_id: str = ""  # For tracing related events

    def __post_init__(self):
        if not self.event_id:
            self.event_id = f"evt_{int(self.timestamp * 1000000)}"

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "priority": self.priority.value,
            "source": self.source,
            "timestamp": self.timestamp,
            "data": self.data,
            "correlation_id": self.correlation_id,
        }


# Type alias for event handlers
EventHandler = Callable[[Event], Coroutine[Any, Any, None]]


class EventBus:
    """Async event bus with priority queue and subscriber management.

    Usage:
        bus = EventBus()
        await bus.start()

        async def on_tool_result(event: Event):
            print(f"Tool result: {event.data}")

        bus.subscribe(EventType.TOOL_RESULT, on_tool_result)
        await bus.publish(Event(
            priority=Priority.NORMAL,
            event_type=EventType.TOOL_RESULT,
            data={"output": "done"}
        ))

        await bus.stop()
    """

    def __init__(self, max_queue_size: int = 1000):
        self._queue: asyncio.PriorityQueue[Event] = asyncio.PriorityQueue(
            maxsize=max_queue_size
        )
        self._subscribers: dict[EventType, list[EventHandler]] = defaultdict(list)
        self._wildcard_subscribers: list[EventHandler] = []
        self._running = False
        self._processor_task: Optional[asyncio.Task] = None
        self._stats = {
            "events_published": 0,
            "events_processed": 0,
            "events_dropped": 0,
            "handlers_errors": 0,
        }
        self._max_queue_size = max_queue_size

    async def start(self) -> None:
        """Start the event bus processor."""
        if self._running:
            return
        self._running = True
        self._processor_task = asyncio.create_task(self._process_loop())
        logger.info("Event bus started")

    async def stop(self) -> None:
        """Stop the event bus processor."""
        self._running = False
        if self._processor_task:
            self._processor_task.cancel()
            try:
                await self._processor_task
            except asyncio.CancelledError:
                pass
        logger.info("Event bus stopped")

    async def publish(self, event: Event) -> bool:
        """Publish an event to the bus.

        Returns True if event was queued, False if dropped.
        """
        if not self._running:
            logger.warning("Event bus not running, dropping event: %s", event.event_type)
            return False

        try:
            self._queue.put_nowait(event)
            self._stats["events_published"] += 1
            return True
        except asyncio.QueueFull:
            self._stats["events_dropped"] += 1
            logger.warning("Event queue full, dropping event: %s", event.event_type)
            return False

    def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        """Subscribe to a specific event type."""
        self._subscribers[event_type].append(handler)

    def subscribe_all(self, handler: EventHandler) -> None:
        """Subscribe to all event types (wildcard)."""
        self._wildcard_subscribers.append(handler)

    def unsubscribe(self, event_type: EventType, handler: EventHandler) -> None:
        """Unsubscribe from a specific event type."""
        if handler in self._subscribers[event_type]:
            self._subscribers[event_type].remove(handler)

    def unsubscribe_all(self, handler: EventHandler) -> None:
        """Unsubscribe from all events."""
        if handler in self._wildcard_subscribers:
            self._wildcard_subscribers.remove(handler)

    async def _process_loop(self) -> None:
        """Main event processing loop."""
        while self._running:
            try:
                event = await asyncio.wait_for(
                    self._queue.get(), timeout=0.1
                )
                await self._dispatch(event)
                self._stats["events_processed"] += 1
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Event processing error: %s", e)
                self._stats["handlers_errors"] += 1

    async def _dispatch(self, event: Event) -> None:
        """Dispatch an event to all matching handlers."""
        handlers = []

        # Specific type handlers
        if event.event_type in self._subscribers:
            handlers.extend(self._subscribers[event.event_type])

        # Wildcard handlers
        handlers.extend(self._wildcard_subscribers)

        for handler in handlers:
            try:
                await handler(event)
            except Exception as e:
                logger.error(
                    "Handler error for %s: %s", event.event_type, e
                )
                self._stats["handlers_errors"] += 1

    @property
    def stats(self) -> dict:
        """Return event bus statistics."""
        return {
            **self._stats,
            "queue_size": self._queue.qsize(),
            "running": self._running,
        }

    @property
    def queue_size(self) -> int:
        """Current queue size."""
        return self._queue.qsize()

    def clear_subscribers(self) -> None:
        """Clear all subscribers."""
        self._subscribers.clear()
        self._wildcard_subscribers.clear()
