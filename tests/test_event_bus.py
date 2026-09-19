"""
Tests for core/event_bus.py — Event Bus with priority queue.
"""

import asyncio
import pytest
import time
from core.event_bus import EventBus, Event, Priority, EventType


@pytest.fixture
def event_bus():
    return EventBus(max_queue_size=100)


class TestEvent:
    def test_event_creation(self):
        event = Event(
            priority=Priority.NORMAL,
            event_type=EventType.USER_INPUT,
            source="test",
            data={"text": "hello"},
        )
        assert event.priority == Priority.NORMAL
        assert event.event_type == EventType.USER_INPUT
        assert event.source == "test"
        assert event.data == {"text": "hello"}
        assert event.event_id.startswith("evt_")

    def test_event_ordering(self):
        e1 = Event(priority=Priority.HIGH)
        e2 = Event(priority=Priority.LOW)
        e3 = Event(priority=Priority.NORMAL)
        # Lower priority value = higher priority = comes first
        assert e1 < e3
        assert e3 < e2

    def test_event_to_dict(self):
        event = Event(
            priority=Priority.HIGH,
            event_type=EventType.TOOL_RESULT,
            data={"output": "done"},
        )
        d = event.to_dict()
        assert d["event_type"] == "tool_result"
        assert d["priority"] == 10
        assert d["data"]["output"] == "done"


class TestEventBus:
    @pytest.mark.asyncio
    async def test_start_stop(self, event_bus):
        assert not event_bus._running
        await event_bus.start()
        assert event_bus._running
        await event_bus.stop()
        assert not event_bus._running

    @pytest.mark.asyncio
    async def test_publish_subscribe(self, event_bus):
        received = []

        async def handler(event: Event):
            received.append(event)

        await event_bus.start()
        event_bus.subscribe(EventType.USER_INPUT, handler)

        event = Event(
            priority=Priority.NORMAL,
            event_type=EventType.USER_INPUT,
            data={"text": "hello"},
        )
        await event_bus.publish(event)

        # Wait for processing
        await asyncio.sleep(0.2)
        assert len(received) == 1
        assert received[0].data == {"text": "hello"}

        await event_bus.stop()

    @pytest.mark.asyncio
    async def test_wildcard_subscriber(self, event_bus):
        received = []

        async def handler(event: Event):
            received.append(event.event_type)

        await event_bus.start()
        event_bus.subscribe_all(handler)

        await event_bus.publish(Event(priority=Priority.NORMAL, event_type=EventType.USER_INPUT))
        await event_bus.publish(Event(priority=Priority.NORMAL, event_type=EventType.TOOL_RESULT))

        await asyncio.sleep(0.2)
        assert EventType.USER_INPUT in received
        assert EventType.TOOL_RESULT in received

        await event_bus.stop()

    @pytest.mark.asyncio
    async def test_priority_ordering(self, event_bus):
        received = []

        async def handler(event: Event):
            received.append(event.priority)

        await event_bus.start()
        event_bus.subscribe_all(handler)

        # Publish in reverse priority order
        await event_bus.publish(Event(priority=Priority.LOW))
        await event_bus.publish(Event(priority=Priority.HIGH))
        await event_bus.publish(Event(priority=Priority.NORMAL))

        await asyncio.sleep(0.3)
        # Should be processed in priority order
        assert received[0] == Priority.HIGH
        assert received[1] == Priority.NORMAL
        assert received[2] == Priority.LOW

        await event_bus.stop()

    @pytest.mark.asyncio
    async def test_unsubscribe(self, event_bus):
        received = []

        async def handler(event: Event):
            received.append(event)

        await event_bus.start()
        event_bus.subscribe(EventType.USER_INPUT, handler)

        await event_bus.publish(Event(priority=Priority.NORMAL, event_type=EventType.USER_INPUT))
        await asyncio.sleep(0.1)
        assert len(received) == 1

        event_bus.unsubscribe(EventType.USER_INPUT, handler)
        await event_bus.publish(Event(priority=Priority.NORMAL, event_type=EventType.USER_INPUT))
        await asyncio.sleep(0.1)
        assert len(received) == 1  # No new event

        await event_bus.stop()

    @pytest.mark.asyncio
    async def test_publish_when_stopped(self, event_bus):
        event = Event(priority=Priority.NORMAL, event_type=EventType.USER_INPUT)
        result = await event_bus.publish(event)
        assert result is False

    @pytest.mark.asyncio
    async def test_stats(self, event_bus):
        await event_bus.start()
        await event_bus.publish(Event(priority=Priority.NORMAL, event_type=EventType.USER_INPUT))
        await asyncio.sleep(0.1)

        stats = event_bus.stats
        assert stats["events_published"] == 1
        assert stats["running"] is True

        await event_bus.stop()

    @pytest.mark.asyncio
    async def test_handler_error_doesnt_crash(self, event_bus):
        async def bad_handler(event: Event):
            raise ValueError("Handler error")

        async def good_handler(event: Event):
            pass

        await event_bus.start()
        event_bus.subscribe(EventType.USER_INPUT, bad_handler)
        event_bus.subscribe(EventType.USER_INPUT, good_handler)

        # Should not raise
        await event_bus.publish(Event(priority=Priority.NORMAL, event_type=EventType.USER_INPUT))
        await asyncio.sleep(0.1)

        assert event_bus.stats["handlers_errors"] == 1
        await event_bus.stop()
