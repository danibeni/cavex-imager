"""Unit tests for LeaseManager service with preemption."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from src.application.services.lease_manager import LeaseManager
from src.domain.exceptions.lease_exceptions import (
    LeaseConflict,
    LeaseExpiredError,
    LeaseNotFound,
)
from src.domain.models.lease import Lease, LeaseStatus


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_acquire_creates_lease_with_priority() -> None:
    """Test acquire creates lease and publishes event."""
    publisher = AsyncMock()
    mgr = LeaseManager(event_publisher=publisher)

    lease = await mgr.acquire(owner="orchestrator", priority=100, ttl_seconds=300)

    assert lease.owner == "orchestrator"
    assert lease.priority == 100
    assert lease.status is LeaseStatus.ACTIVE
    publisher.publish.assert_awaited()


@pytest.mark.asyncio
async def test_acquire_preempts_lower_priority_lease() -> None:
    """Test higher priority acquire preempts existing lease."""
    publisher = AsyncMock()
    mgr = LeaseManager(event_publisher=publisher)

    old_lease = await mgr.acquire(owner="engineer", priority=50, ttl_seconds=300)
    new_lease = await mgr.acquire(owner="orchestrator", priority=100, ttl_seconds=300)

    assert old_lease.status is LeaseStatus.PREEMPTED
    assert new_lease.owner == "orchestrator"
    assert new_lease.status is LeaseStatus.ACTIVE
    assert mgr.get_current_lease() is new_lease


@pytest.mark.asyncio
async def test_acquire_rejects_equal_priority() -> None:
    """Test acquire fails when existing lease has equal priority."""
    publisher = AsyncMock()
    mgr = LeaseManager(event_publisher=publisher)
    await mgr.acquire(owner="client_a", priority=50, ttl_seconds=300)

    with pytest.raises(LeaseConflict):
        await mgr.acquire(owner="client_b", priority=50, ttl_seconds=300)


@pytest.mark.asyncio
async def test_acquire_rejects_lower_priority() -> None:
    """Test acquire fails when existing lease has higher priority."""
    publisher = AsyncMock()
    mgr = LeaseManager(event_publisher=publisher)
    await mgr.acquire(owner="orchestrator", priority=100, ttl_seconds=300)

    with pytest.raises(LeaseConflict):
        await mgr.acquire(owner="engineer", priority=50, ttl_seconds=300)


@pytest.mark.asyncio
async def test_release_clears_lease() -> None:
    """Test release updates status and clears current lease."""
    publisher = AsyncMock()
    mgr = LeaseManager(event_publisher=publisher)
    lease = await mgr.acquire(owner="orchestrator", priority=100, ttl_seconds=300)

    await mgr.release(lease.id)

    assert mgr.get_current_lease() is None
    assert lease.status is LeaseStatus.RELEASED


@pytest.mark.asyncio
async def test_release_raises_if_not_found() -> None:
    """Test release fails for non-existing lease."""
    publisher = AsyncMock()
    mgr = LeaseManager(event_publisher=publisher)

    with pytest.raises(LeaseNotFound):
        await mgr.release("missing-lease")


@pytest.mark.asyncio
async def test_renew_extends_expiration() -> None:
    """Test renew extends lease expiration."""
    publisher = AsyncMock()
    mgr = LeaseManager(event_publisher=publisher)
    lease = await mgr.acquire(owner="orchestrator", priority=100, ttl_seconds=120)
    old_expiration = lease.expires_at

    renewed = await mgr.renew(lease.id, ttl_seconds=360)

    assert renewed.expires_at > old_expiration


@pytest.mark.asyncio
async def test_renew_respects_max_ttl() -> None:
    """Test renew caps TTL at max_ttl."""
    publisher = AsyncMock()
    mgr = LeaseManager(event_publisher=publisher, max_ttl=600)
    lease = await mgr.acquire(owner="orchestrator", priority=100, ttl_seconds=120)

    renewed = await mgr.renew(lease.id, ttl_seconds=9999)

    assert renewed.ttl_seconds == 600


@pytest.mark.asyncio
async def test_renew_raises_if_expired() -> None:
    """Test renew fails for expired lease."""
    publisher = AsyncMock()
    mgr = LeaseManager(event_publisher=publisher)

    # Manually inject expired lease
    now = _now()
    mgr._current_lease = Lease(
        id="expired-1",
        owner="test",
        priority=10,
        acquired_at=now - timedelta(minutes=10),
        expires_at=now - timedelta(seconds=1),
        ttl_seconds=120,
        status=LeaseStatus.ACTIVE,
    )

    with pytest.raises(LeaseExpiredError):
        await mgr.renew("expired-1", ttl_seconds=120)


def test_get_current_lease_returns_none_if_expired() -> None:
    """Test get_current_lease returns None for expired lease."""
    publisher = AsyncMock()
    mgr = LeaseManager(event_publisher=publisher)
    now = _now()
    mgr._current_lease = Lease(
        id="lease-1",
        owner="orchestrator",
        priority=100,
        acquired_at=now - timedelta(minutes=10),
        expires_at=now - timedelta(seconds=1),
        ttl_seconds=120,
        status=LeaseStatus.ACTIVE,
    )

    current = mgr.get_current_lease()

    assert current is None


def test_get_current_lease_returns_none_if_preempted() -> None:
    """Test get_current_lease returns None for preempted lease."""
    publisher = AsyncMock()
    mgr = LeaseManager(event_publisher=publisher)
    now = _now()
    mgr._current_lease = Lease(
        id="lease-1",
        owner="engineer",
        priority=50,
        acquired_at=now,
        expires_at=now + timedelta(seconds=300),
        ttl_seconds=300,
        status=LeaseStatus.PREEMPTED,
    )

    current = mgr.get_current_lease()

    assert current is None


@pytest.mark.asyncio
async def test_require_lease_returns_active_lease() -> None:
    """Test require_lease returns matching active lease."""
    publisher = AsyncMock()
    mgr = LeaseManager(event_publisher=publisher)
    lease = await mgr.acquire(owner="test", priority=50, ttl_seconds=300)

    result = mgr.require_lease(lease.id)

    assert result is lease


@pytest.mark.asyncio
async def test_require_lease_raises_on_mismatch() -> None:
    """Test require_lease raises when lease_id doesn't match."""
    publisher = AsyncMock()
    mgr = LeaseManager(event_publisher=publisher)
    await mgr.acquire(owner="test", priority=50, ttl_seconds=300)

    with pytest.raises(LeaseNotFound):
        mgr.require_lease("wrong-id")


@pytest.mark.asyncio
async def test_renew_expired_lease_raises_lease_expired_error() -> None:
    """Test renew raises LeaseExpiredError when lease is already expired."""
    publisher = AsyncMock()
    mgr = LeaseManager(event_publisher=publisher)
    lease = await mgr.acquire(owner="test", priority=50, ttl_seconds=120)

    # Force-expire the lease by backdating expires_at
    from datetime import timedelta
    lease.expires_at = lease.acquired_at - timedelta(seconds=1)

    with pytest.raises(LeaseExpiredError):
        await mgr.renew(lease.id)

    assert mgr.get_current_lease() is None


def test_preempt_mode_property_returns_configured_mode() -> None:
    """Test preempt_mode property returns the value set at construction."""
    publisher = AsyncMock()
    mgr = LeaseManager(event_publisher=publisher, preempt_mode="abort")

    assert mgr.preempt_mode == "abort"


@pytest.mark.asyncio
async def test_renew_raises_lease_not_found_on_wrong_id() -> None:
    """Test renew raises LeaseNotFound when lease_id does not match active lease."""
    publisher = AsyncMock()
    mgr = LeaseManager(event_publisher=publisher)
    await mgr.acquire(owner="test", priority=50, ttl_seconds=120)

    with pytest.raises(LeaseNotFound):
        await mgr.renew("wrong-lease-id")
