"""Lease management application service with priority preemption."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import structlog

from src.domain.exceptions.lease_exceptions import (
    LeaseConflict,
    LeaseExpiredError,
    LeaseNotFound,
)
from src.domain.models.lease import Lease, LeaseStatus
from src.domain.ports.event_publisher import IEventPublisher

logger = structlog.get_logger(__name__)


def _now_utc() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(timezone.utc)


class LeaseManager:
    """Service that coordinates lease lifecycle with priority preemption."""

    def __init__(
        self,
        event_publisher: IEventPublisher,
        default_ttl: int = 120,
        max_ttl: int = 600,
        preempt_mode: str = "graceful",
    ) -> None:
        """Initialize lease manager.

        Args:
            event_publisher: Port used to publish lease events.
            default_ttl: Default TTL in seconds.
            max_ttl: Maximum allowed TTL in seconds.
            preempt_mode: Preemption mode ('graceful' or 'abort').
        """
        self._event_publisher = event_publisher
        self._current_lease: Lease | None = None
        self._default_ttl = default_ttl
        self._max_ttl = max_ttl
        self._preempt_mode = preempt_mode

    async def acquire(
        self,
        owner: str,
        priority: int,
        ttl_seconds: int | None = None,
    ) -> Lease:
        """Acquire a new exclusive lease with priority-based preemption.

        Args:
            owner: Client identifier requesting the lease.
            priority: Priority level (higher number = higher priority).
            ttl_seconds: Lease duration in seconds (uses default if None).

        Returns:
            The created lease entity.

        Raises:
            LeaseConflict: If existing lease has equal or higher priority.
        """
        ttl = min(ttl_seconds or self._default_ttl, self._max_ttl)
        existing = self.get_current_lease()

        if existing is not None:
            if priority <= existing.priority:
                logger.warning(
                    "lease_conflict",
                    owner=owner,
                    priority=priority,
                    current_owner=existing.owner,
                    current_priority=existing.priority,
                )
                raise LeaseConflict(
                    f"Cannot acquire: existing lease has priority {existing.priority}"
                )

            # Preempt current lease
            old_owner = existing.owner
            old_id = existing.id
            existing.mark_preempted()

            await self._event_publisher.publish(
                "lease.preempted",
                {
                    "old_lease_id": old_id,
                    "old_owner": old_owner,
                    "new_owner": owner,
                    "new_priority": priority,
                    "preempt_mode": self._preempt_mode,
                },
            )
            logger.info(
                "lease_preempted",
                old_lease_id=old_id,
                old_owner=old_owner,
                new_owner=owner,
            )

        now = _now_utc()
        lease = Lease(
            id=f"lease_{uuid4().hex[:8]}",
            owner=owner,
            priority=priority,
            acquired_at=now,
            expires_at=now + timedelta(seconds=ttl),
            ttl_seconds=ttl,
            status=LeaseStatus.ACTIVE,
        )
        self._current_lease = lease

        await self._event_publisher.publish(
            "lease.acquired",
            {
                "lease_id": lease.id,
                "owner": lease.owner,
                "priority": lease.priority,
                "expires_at": lease.expires_at.isoformat(),
            },
        )
        logger.info("lease_acquired", lease_id=lease.id, owner=owner, priority=priority)
        return lease

    async def release(self, lease_id: str) -> None:
        """Release the active lease.

        Args:
            lease_id: Lease identifier to release.

        Raises:
            LeaseNotFound: If lease does not exist or does not match.
        """
        if self._current_lease is None or self._current_lease.id != lease_id:
            raise LeaseNotFound("Lease not found.")

        lease = self._current_lease
        lease.status = LeaseStatus.RELEASED
        self._current_lease = None

        await self._event_publisher.publish(
            "lease.released",
            {"lease_id": lease.id, "owner": lease.owner},
        )
        logger.info("lease_released", lease_id=lease.id, owner=lease.owner)

    async def renew(self, lease_id: str, ttl_seconds: int | None = None) -> Lease:
        """Renew the active lease TTL.

        Args:
            lease_id: Lease identifier to renew.
            ttl_seconds: New TTL in seconds (uses default if None).

        Returns:
            Updated lease entity.

        Raises:
            LeaseNotFound: If lease does not exist or does not match.
            LeaseExpiredError: If lease is already expired.
        """
        lease = self._current_lease
        if lease is None or lease.id != lease_id:
            raise LeaseNotFound("Lease not found.")
        if lease.is_expired():
            lease.status = LeaseStatus.EXPIRED
            self._current_lease = None
            raise LeaseExpiredError("Cannot renew an expired lease.")

        ttl = min(ttl_seconds or self._default_ttl, self._max_ttl)
        lease.renew(ttl)

        await self._event_publisher.publish(
            "lease.renewed",
            {
                "lease_id": lease.id,
                "owner": lease.owner,
                "expires_at": lease.expires_at.isoformat(),
            },
        )
        logger.info("lease_renewed", lease_id=lease.id, expires_at=lease.expires_at.isoformat())
        return lease

    def get_current_lease(self) -> Lease | None:
        """Return active lease if still valid, otherwise None."""
        if self._current_lease is None:
            return None
        if self._current_lease.is_expired():
            self._current_lease.status = LeaseStatus.EXPIRED
            logger.info("lease_expired", lease_id=self._current_lease.id)
            self._current_lease = None
            return None
        if self._current_lease.status == LeaseStatus.PREEMPTED:
            return None
        return self._current_lease

    def require_lease(self, lease_id: str) -> Lease:
        """Validate that given lease_id matches the active lease.

        Args:
            lease_id: Expected active lease identifier.

        Returns:
            The active lease.

        Raises:
            LeaseNotFound: If no active lease or lease_id mismatch.
        """
        lease = self.get_current_lease()
        if lease is None or lease.id != lease_id:
            raise LeaseNotFound("No active lease or lease_id mismatch.")
        return lease

    @property
    def preempt_mode(self) -> str:
        """Return configured preemption mode."""
        return self._preempt_mode
