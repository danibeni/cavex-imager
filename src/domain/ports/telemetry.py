"""Telemetry port definitions (ITelemetryPort in architecture spec)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import WebSocket


class ITelemetryPublisher(ABC):
    """Interface for publishing telemetry to WebSocket clients."""

    @abstractmethod
    async def connect(self, websocket: "WebSocket", rate_hz: int) -> bool:
        """Register a new WebSocket client.

        Args:
            websocket: The WebSocket connection to register.
            rate_hz: Desired update rate (1 or 2 Hz).

        Returns:
            True if the client was accepted, False if capacity is full.
        """

    @abstractmethod
    def disconnect(self, websocket: "WebSocket") -> None:
        """Remove a WebSocket client from the active connection registry.

        Args:
            websocket: The WebSocket connection to remove.
        """

    @abstractmethod
    def update_subscriptions(self, websocket: "WebSocket", topics: list[str]) -> None:
        """Update topic subscriptions for a specific client.

        Args:
            websocket: The client whose subscriptions should be updated.
            topics: List of topic names. Empty list or ["*"] means all topics.
        """

    @abstractmethod
    async def publish_snapshot(self, snapshot: dict[str, Any]) -> None:
        """Broadcast a periodic telemetry snapshot to all clients.

        Args:
            snapshot: Complete camera status snapshot.
        """

    @abstractmethod
    async def publish_event(self, event_type: str, payload: dict[str, Any]) -> None:
        """Broadcast a discrete event to all clients.

        Args:
            event_type: Event type (capture_event, lease_event, alarm_event).
            payload: Event data.
        """

    @abstractmethod
    def client_count(self) -> int:
        """Return number of connected WebSocket clients."""
