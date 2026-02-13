"""Telemetry port definitions (ITelemetryPort in architecture spec)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ITelemetryPublisher(ABC):
    """Interface for publishing telemetry to WebSocket clients."""

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
