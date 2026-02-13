"""Unit tests for Lease model."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.domain.models.lease import Lease, LeaseStatus


def _now() -> datetime:
    return datetime.now(timezone.utc)


def test_lease_creation_with_valid_values() -> None:
    """Test lease creation with valid values including priority."""
    now = _now()
    lease = Lease(
        id="lease-1",
        owner="orchestrator",
        priority=100,
        acquired_at=now,
        expires_at=now + timedelta(seconds=300),
        ttl_seconds=300,
        status=LeaseStatus.ACTIVE,
    )

    assert lease.id == "lease-1"
    assert lease.owner == "orchestrator"
    assert lease.priority == 100
    assert lease.ttl_seconds == 300
    assert lease.status is LeaseStatus.ACTIVE
    assert lease.preempted is False


def test_is_expired_returns_true_when_expired() -> None:
    """Test that is_expired returns True when expires_at is in the past."""
    now = _now()
    lease = Lease(
        id="lease-2",
        owner="operator",
        priority=50,
        acquired_at=now - timedelta(minutes=10),
        expires_at=now - timedelta(seconds=1),
        ttl_seconds=120,
        status=LeaseStatus.ACTIVE,
    )

    assert lease.is_expired() is True


def test_is_expired_returns_false_when_active() -> None:
    """Test that is_expired returns False when lease is still valid."""
    now = _now()
    lease = Lease(
        id="lease-2b",
        owner="operator",
        priority=50,
        acquired_at=now,
        expires_at=now + timedelta(seconds=300),
        ttl_seconds=300,
        status=LeaseStatus.ACTIVE,
    )

    assert lease.is_expired() is False


def test_renew_extends_expiration() -> None:
    """Test that renew extends lease expiration."""
    now = _now()
    lease = Lease(
        id="lease-3",
        owner="engineer",
        priority=50,
        acquired_at=now,
        expires_at=now + timedelta(seconds=10),
        ttl_seconds=10,
        status=LeaseStatus.ACTIVE,
    )
    old_expiration = lease.expires_at

    lease.renew(ttl_seconds=120)

    assert lease.expires_at > old_expiration
    assert lease.ttl_seconds == 120


def test_mark_preempted_updates_state() -> None:
    """Test that mark_preempted sets correct status."""
    now = _now()
    lease = Lease(
        id="lease-4",
        owner="engineer",
        priority=50,
        acquired_at=now,
        expires_at=now + timedelta(seconds=300),
        ttl_seconds=300,
        status=LeaseStatus.ACTIVE,
    )

    lease.mark_preempted()

    assert lease.status is LeaseStatus.PREEMPTED
    assert lease.preempted is True


def test_remaining_seconds_returns_positive_value() -> None:
    """Test remaining_seconds for an active lease."""
    now = _now()
    lease = Lease(
        id="lease-5",
        owner="test",
        priority=10,
        acquired_at=now,
        expires_at=now + timedelta(seconds=60),
        ttl_seconds=60,
        status=LeaseStatus.ACTIVE,
    )

    assert lease.remaining_seconds() > 0


def test_remaining_seconds_returns_zero_when_expired() -> None:
    """Test remaining_seconds for an expired lease."""
    now = _now()
    lease = Lease(
        id="lease-6",
        owner="test",
        priority=10,
        acquired_at=now - timedelta(minutes=10),
        expires_at=now - timedelta(seconds=1),
        ttl_seconds=60,
        status=LeaseStatus.ACTIVE,
    )

    assert lease.remaining_seconds() == 0.0


def test_status_validation_rejects_invalid_value() -> None:
    """Test that invalid status value raises ValueError."""
    now = _now()

    with pytest.raises(ValueError):
        Lease(
            id="lease-bad",
            owner="invalid",
            priority=10,
            acquired_at=now,
            expires_at=now + timedelta(seconds=300),
            ttl_seconds=300,
            status="ACTIVE",  # type: ignore[arg-type]
        )


def test_priority_validation_rejects_negative() -> None:
    """Test that negative priority raises ValueError."""
    now = _now()

    with pytest.raises(ValueError, match="priority"):
        Lease(
            id="lease-bad",
            owner="invalid",
            priority=-1,
            acquired_at=now,
            expires_at=now + timedelta(seconds=300),
            ttl_seconds=300,
            status=LeaseStatus.ACTIVE,
        )


def test_ttl_validation_rejects_zero() -> None:
    """Test that zero ttl_seconds raises ValueError."""
    now = _now()

    with pytest.raises(ValueError, match="ttl_seconds"):
        Lease(
            id="lease-bad",
            owner="invalid",
            priority=10,
            acquired_at=now,
            expires_at=now + timedelta(seconds=300),
            ttl_seconds=0,
            status=LeaseStatus.ACTIVE,
        )
