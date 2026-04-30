"""Unit tests for lease domain events."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.domain.events.lease_events import (
    LeaseAcquired,
    LeaseExpired,
    LeasePreempted,
    LeaseReleased,
    LeaseRenewed,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def test_lease_acquired_stores_fields() -> None:
    """Test LeaseAcquired dataclass stores all fields."""
    ts = _now()
    event = LeaseAcquired(
        lease_id="lease_abc",
        owner="orchestrator",
        priority=100,
        timestamp=ts,
    )

    assert event.lease_id == "lease_abc"
    assert event.owner == "orchestrator"
    assert event.priority == 100
    assert event.timestamp == ts


def test_lease_acquired_is_immutable() -> None:
    """Test LeaseAcquired is a frozen dataclass."""
    event = LeaseAcquired(
        lease_id="l1", owner="op", priority=50, timestamp=_now()
    )

    with pytest.raises((AttributeError, TypeError)):
        event.owner = "other"  # type: ignore[misc]


def test_lease_renewed_stores_new_expiry() -> None:
    """Test LeaseRenewed stores the new expiration timestamp."""
    now = _now()
    event = LeaseRenewed(
        lease_id="lease_abc",
        owner="orchestrator",
        new_expires_at=now + timedelta(seconds=120),
        timestamp=now,
    )

    assert event.new_expires_at > event.timestamp


def test_lease_preempted_stores_old_and_new_owners() -> None:
    """Test LeasePreempted stores both old and new owner information."""
    event = LeasePreempted(
        old_lease_id="lease_old",
        old_owner="operator",
        new_owner="orchestrator",
        new_priority=200,
        timestamp=_now(),
    )

    assert event.old_owner == "operator"
    assert event.new_owner == "orchestrator"
    assert event.new_priority == 200


def test_lease_released_stores_fields() -> None:
    """Test LeaseReleased stores lease_id and owner."""
    event = LeaseReleased(
        lease_id="lease_abc",
        owner="orchestrator",
        timestamp=_now(),
    )

    assert event.lease_id == "lease_abc"
    assert event.owner == "orchestrator"


def test_lease_expired_stores_fields() -> None:
    """Test LeaseExpired stores lease_id and owner."""
    event = LeaseExpired(
        lease_id="lease_old",
        owner="operator",
        timestamp=_now(),
    )

    assert event.lease_id == "lease_old"
    assert event.owner == "operator"


def test_events_equality_based_on_fields() -> None:
    """Test that two events with identical fields compare equal."""
    ts = _now()
    a = LeaseExpired(lease_id="x", owner="op", timestamp=ts)
    b = LeaseExpired(lease_id="x", owner="op", timestamp=ts)

    assert a == b
