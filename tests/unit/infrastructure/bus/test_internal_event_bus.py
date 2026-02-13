"""Unit tests for internal event bus."""

from __future__ import annotations

from typing import Any

import pytest

from src.infrastructure.bus.internal_event_bus import InternalEventBus


@pytest.mark.asyncio
async def test_subscribe_registers_handler_correctly() -> None:
    """Test subscribe registers a handler in the registry."""
    bus = InternalEventBus()

    async def handler(payload: dict[str, Any]) -> None:
        del payload

    await bus.subscribe("lease.acquired", handler)

    assert "lease.acquired" in bus._handlers
    assert len(bus._handlers["lease.acquired"]) == 1


@pytest.mark.asyncio
async def test_publish_calls_all_registered_handlers() -> None:
    """Test publish dispatches event payload to all handlers."""
    bus = InternalEventBus()
    received: list[dict[str, Any]] = []

    async def handler_a(payload: dict[str, Any]) -> None:
        received.append({"handler": "a", **payload})

    async def handler_b(payload: dict[str, Any]) -> None:
        received.append({"handler": "b", **payload})

    await bus.subscribe("capture.completed", handler_a)
    await bus.subscribe("capture.completed", handler_b)
    await bus.publish("capture.completed", {"capture_id": "c1"})

    assert len(received) == 2
    assert {item["handler"] for item in received} == {"a", "b"}


@pytest.mark.asyncio
async def test_publish_does_not_fail_without_handlers() -> None:
    """Test publish ignores event types without handlers."""
    bus = InternalEventBus()
    await bus.publish("unknown.event", {"value": 1})


@pytest.mark.asyncio
async def test_publish_continues_after_handler_error() -> None:
    """Test publish continues to other handlers after one raises."""
    bus = InternalEventBus()
    called: list[str] = []

    async def failing_handler(payload: dict[str, Any]) -> None:
        raise RuntimeError("handler error")

    async def working_handler(payload: dict[str, Any]) -> None:
        called.append("ok")

    await bus.subscribe("test.event", failing_handler)
    await bus.subscribe("test.event", working_handler)
    await bus.publish("test.event", {})

    assert called == ["ok"]
