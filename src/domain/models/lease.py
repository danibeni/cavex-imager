"""Lease entity for exclusive device control with priority preemption."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum


class LeaseStatus(str, Enum):
    """Lease lifecycle states."""

    ACTIVE = "active"
    PREEMPTED = "preempted"
    EXPIRED = "expired"
    RELEASED = "released"


@dataclass
class Lease:
    """Represents an exclusive control lease with priority."""

    id: str
    owner: str
    priority: int
    acquired_at: datetime
    expires_at: datetime | None  # None for permanent leases
    ttl_seconds: int | None  # None for permanent leases
    status: LeaseStatus
    permanent: bool = field(default=False)

    def __post_init__(self) -> None:
        """Validate lease invariants after construction."""
        if not isinstance(self.status, LeaseStatus):
            raise ValueError("status must be an instance of LeaseStatus")
        if self.priority < 0:
            raise ValueError("priority must be non-negative")
        if not self.permanent and (self.ttl_seconds is None or self.ttl_seconds <= 0):
            raise ValueError("ttl_seconds must be positive for non-permanent leases")

    @staticmethod
    def now_utc() -> datetime:
        """Return current UTC datetime."""
        return datetime.now(timezone.utc)

    def is_expired(self) -> bool:
        """Check whether the lease expiration time has passed.

        Permanent leases never expire.
        """
        if self.permanent:
            return False
        return self.now_utc() > self.expires_at  # type: ignore[operator]

    def renew(self, ttl_seconds: int) -> None:
        """Extend lease expiration from current time.

        Args:
            ttl_seconds: New TTL in seconds from now.
        """
        now = self.now_utc()
        self.ttl_seconds = ttl_seconds
        self.expires_at = now + timedelta(seconds=ttl_seconds)
        self.permanent = False

    def mark_preempted(self) -> None:
        """Mark lease as preempted by a higher-priority client."""
        self.status = LeaseStatus.PREEMPTED

    def remaining_seconds(self) -> float | None:
        """Return seconds until expiration, zero if expired, None if permanent."""
        if self.permanent:
            return None
        delta = (self.expires_at - self.now_utc()).total_seconds()  # type: ignore[operator]
        return max(0.0, delta)
