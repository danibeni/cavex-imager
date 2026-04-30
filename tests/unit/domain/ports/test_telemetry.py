"""Unit tests for ITelemetryPublisher abstract port."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from src.domain.ports.telemetry import ITelemetryPublisher

if TYPE_CHECKING:
    from fastapi import WebSocket


class _ConcretePublisher(ITelemetryPublisher):
    """Minimal concrete implementation used only in tests."""

    def __init__(self) -> None:
        self.snapshots: list[dict[str, Any]] = []
        self.events: list[tuple[str, dict[str, Any]]] = []
        self._client_count = 0

    async def connect(self, websocket: "WebSocket", rate_hz: int) -> bool:
        return True

    def disconnect(self, websocket: "WebSocket") -> None:
        pass

    def update_subscriptions(self, websocket: "WebSocket", topics: list[str]) -> None:
        pass

    async def publish_snapshot(self, snapshot: dict[str, Any]) -> None:
        self.snapshots.append(snapshot)

    async def publish_event(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append((event_type, payload))

    def client_count(self) -> int:
        return self._client_count


def test_cannot_instantiate_abstract_class() -> None:
    """Test ITelemetryPublisher cannot be instantiated directly."""
    with pytest.raises(TypeError):
        ITelemetryPublisher()  # type: ignore[abstract]


@pytest.mark.asyncio
async def test_concrete_publisher_publish_snapshot() -> None:
    """Test concrete implementation stores snapshots via publish_snapshot."""
    publisher = _ConcretePublisher()
    snapshot = {"device": {"connected": True}, "status": {"state": "IDLE"}}

    await publisher.publish_snapshot(snapshot)

    assert len(publisher.snapshots) == 1
    assert publisher.snapshots[0] == snapshot


@pytest.mark.asyncio
async def test_concrete_publisher_publish_event() -> None:
    """Test concrete implementation stores events via publish_event."""
    publisher = _ConcretePublisher()

    await publisher.publish_event("capture.started", {"capture_id": "exp_001"})

    assert len(publisher.events) == 1
    assert publisher.events[0] == ("capture.started", {"capture_id": "exp_001"})


def test_concrete_publisher_client_count() -> None:
    """Test concrete implementation returns client count."""
    publisher = _ConcretePublisher()
    publisher._client_count = 3

    assert publisher.client_count() == 3


@pytest.mark.asyncio
async def test_mock_publisher_satisfies_interface() -> None:
    """Test that AsyncMock can stand in for ITelemetryPublisher in service tests."""
    publisher = AsyncMock(spec=ITelemetryPublisher)
    publisher.client_count.return_value = 2

    await publisher.publish_snapshot({"state": "IDLE"})
    await publisher.publish_event("lease.acquired", {"lease_id": "l1"})

    publisher.publish_snapshot.assert_awaited_once()
    publisher.publish_event.assert_awaited_once()
    assert publisher.client_count() == 2
