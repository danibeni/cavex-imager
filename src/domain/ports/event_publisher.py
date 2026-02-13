"""Domain event publisher port definitions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable


class IEventPublisher(ABC):
    """Interface for publishing and subscribing domain events."""

    @abstractmethod
    async def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        """Publish a domain event.

        Args:
            event_type: Event type name.
            payload: Event payload data.
        """

    @abstractmethod
    async def subscribe(
        self,
        event_type: str,
        handler: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        """Subscribe an async handler to an event type.

        Args:
            event_type: Event type name to subscribe.
            handler: Async callback for event payload.
        """
