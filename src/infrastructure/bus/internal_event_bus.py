"""In-memory internal event bus with error resilience."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from src.domain.ports.event_publisher import IEventPublisher

logger = structlog.get_logger(__name__)


class InternalEventBus(IEventPublisher):
    """In-memory event bus for local domain event dispatching.

    Handlers that raise exceptions are logged but do not block
    other handlers from receiving the event.
    """

    def __init__(self) -> None:
        """Initialize event handler registry."""
        self._handlers: dict[str, list[Callable[[dict[str, Any]], Awaitable[None]]]] = {}

    async def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        """Publish an event to all registered handlers.

        Errors in individual handlers are caught and logged.

        Args:
            event_type: Event type key.
            payload: Event payload dictionary.
        """
        handlers = self._handlers.get(event_type, [])
        logger.debug("event_publish", event_type=event_type, handlers=len(handlers))
        for handler in handlers:
            try:
                await handler(payload)
            except Exception as exc:
                logger.exception(
                    "event_handler_error",
                    event_type=event_type,
                    handler=getattr(handler, "__name__", str(handler)),
                    error=str(exc),
                )

    async def subscribe(
        self, event_type: str, handler: Callable[[dict[str, Any]], Awaitable[None]]
    ) -> None:
        """Register an async handler for an event type.

        Args:
            event_type: Event type key.
            handler: Async payload handler.
        """
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)
        logger.debug(
            "event_subscribe",
            event_type=event_type,
            handlers=len(self._handlers[event_type]),
        )
