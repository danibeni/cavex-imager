"""Domain events related to lease lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class LeaseAcquired:
    """Event emitted when a lease is acquired."""

    lease_id: str
    owner: str
    priority: int
    timestamp: datetime


@dataclass(frozen=True)
class LeaseRenewed:
    """Event emitted when a lease is renewed."""

    lease_id: str
    owner: str
    new_expires_at: datetime
    timestamp: datetime


@dataclass(frozen=True)
class LeasePreempted:
    """Event emitted when a lease is preempted by higher priority."""

    old_lease_id: str
    old_owner: str
    new_owner: str
    new_priority: int
    timestamp: datetime


@dataclass(frozen=True)
class LeaseReleased:
    """Event emitted when a lease is released."""

    lease_id: str
    owner: str
    timestamp: datetime


@dataclass(frozen=True)
class LeaseExpired:
    """Event emitted when a lease expires."""

    lease_id: str
    owner: str
    timestamp: datetime
