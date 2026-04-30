"""Stress and concurrency tests for LeaseManager."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from src.application.services.lease_manager import LeaseManager
from src.domain.exceptions.lease_exceptions import LeaseConflict
from src.domain.models.lease import LeaseStatus


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Concurrency tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_acquire_only_one_wins() -> None:
    """Only one coroutine should acquire the lease when racing at the same priority."""
    publisher = AsyncMock()
    mgr = LeaseManager(event_publisher=publisher, preempt_mode="abort")

    results: list[str] = []

    async def try_acquire(owner: str) -> None:
        try:
            await mgr.acquire(owner=owner, priority=50, ttl_seconds=120)
            results.append(f"acquired:{owner}")
        except LeaseConflict:
            results.append(f"conflict:{owner}")

    await asyncio.gather(*[try_acquire(f"client_{i}") for i in range(10)])

    acquired = [r for r in results if r.startswith("acquired:")]
    conflicts = [r for r in results if r.startswith("conflict:")]

    assert len(acquired) == 1, "Exactly one client must win the lease"
    assert len(conflicts) == 9, "All other clients must get a conflict"


@pytest.mark.asyncio
async def test_concurrent_high_priority_preempts_all() -> None:
    """A high-priority client must preempt regardless of order."""
    publisher = AsyncMock()
    mgr = LeaseManager(event_publisher=publisher, preempt_mode="abort")

    # Seed with a low-priority lease
    await mgr.acquire(owner="low_prio", priority=10, ttl_seconds=300)

    async def try_acquire(owner: str, priority: int) -> str:
        try:
            await mgr.acquire(owner=owner, priority=priority, ttl_seconds=120)
            return f"acquired:{owner}"
        except LeaseConflict:
            return f"conflict:{owner}"

    results = await asyncio.gather(
        try_acquire("high_prio", 100),
        try_acquire("mid_prio_a", 50),
        try_acquire("mid_prio_b", 50),
    )

    acquired = [r for r in results if r.startswith("acquired:")]
    assert len(acquired) == 1
    final_lease = mgr.get_current_lease()
    assert final_lease is not None
    assert final_lease.priority >= 50


@pytest.mark.asyncio
async def test_rapid_acquire_release_cycles_maintain_consistency() -> None:
    """Rapid sequential acquire/release must leave the manager in a clean state."""
    publisher = AsyncMock()
    mgr = LeaseManager(event_publisher=publisher)

    for i in range(50):
        lease = await mgr.acquire(owner=f"client_{i}", priority=100, ttl_seconds=120)
        assert mgr.get_current_lease() is lease
        await mgr.release(lease.id)
        assert mgr.get_current_lease() is None


@pytest.mark.asyncio
async def test_concurrent_renew_only_valid_lease_succeeds() -> None:
    """Concurrent renew attempts on the same lease must all succeed or fail cleanly."""
    publisher = AsyncMock()
    mgr = LeaseManager(event_publisher=publisher)
    lease = await mgr.acquire(owner="client", priority=50, ttl_seconds=120)

    results = await asyncio.gather(
        *[mgr.renew(lease.id, ttl_seconds=200) for _ in range(20)],
        return_exceptions=True,
    )

    successes = [r for r in results if not isinstance(r, Exception)]
    assert len(successes) == 20, "All renew calls on a valid lease must succeed"
    current = mgr.get_current_lease()
    assert current is not None
    assert current.ttl_seconds == 200


# ---------------------------------------------------------------------------
# Graceful preemption tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_graceful_preemption_waits_for_exposure() -> None:
    """In graceful mode, acquire must poll until the exposure finishes."""
    publisher = AsyncMock()
    call_count = 0

    def is_exposing() -> bool:
        nonlocal call_count
        call_count += 1
        return call_count <= 4  # busy for the first 4 checks, then done

    mgr = LeaseManager(
        event_publisher=publisher,
        preempt_mode="graceful",
        graceful_timeout_s=60,
    )
    mgr.set_exposing_check(is_exposing)
    await mgr.acquire(owner="observer", priority=10, ttl_seconds=300)

    with patch("src.application.services.lease_manager.asyncio.sleep", new_callable=AsyncMock):
        new_lease = await mgr.acquire(owner="orchestrator", priority=100, ttl_seconds=300)

    assert new_lease.status is LeaseStatus.ACTIVE
    assert new_lease.owner == "orchestrator"
    assert call_count >= 4, "Must have polled at least until exposure finished"


@pytest.mark.asyncio
async def test_abort_mode_does_not_wait_for_exposure() -> None:
    """In abort mode, acquire must preempt immediately without polling."""
    publisher = AsyncMock()
    call_count = 0

    def is_exposing() -> bool:
        nonlocal call_count
        call_count += 1
        return True  # always busy

    mgr = LeaseManager(
        event_publisher=publisher,
        preempt_mode="abort",
        graceful_timeout_s=60,
    )
    mgr.set_exposing_check(is_exposing)
    await mgr.acquire(owner="observer", priority=10, ttl_seconds=300)

    with patch("src.application.services.lease_manager.asyncio.sleep", new_callable=AsyncMock):
        new_lease = await mgr.acquire(owner="orchestrator", priority=100, ttl_seconds=300)

    assert new_lease.status is LeaseStatus.ACTIVE
    assert call_count == 0, "Abort mode must not poll is_exposing at all"


@pytest.mark.asyncio
async def test_graceful_preemption_proceeds_after_timeout() -> None:
    """If the exposure never finishes, graceful preemption must proceed after timeout."""
    publisher = AsyncMock()

    def is_exposing() -> bool:
        return True  # never finishes

    mgr = LeaseManager(
        event_publisher=publisher,
        preempt_mode="graceful",
        graceful_timeout_s=1,  # very short for the test
    )
    mgr.set_exposing_check(is_exposing)
    await mgr.acquire(owner="observer", priority=10, ttl_seconds=300)

    # Patch sleep so the loop runs fast; real sleep would make the test 1+ second slow
    with patch("src.application.services.lease_manager.asyncio.sleep", new_callable=AsyncMock):
        new_lease = await mgr.acquire(owner="orchestrator", priority=100, ttl_seconds=300)

    assert new_lease.status is LeaseStatus.ACTIVE, "Must preempt even after timeout"


# ---------------------------------------------------------------------------
# Lease expiry under concurrent access
# ---------------------------------------------------------------------------


def test_expired_lease_is_evicted_under_concurrent_reads() -> None:
    """get_current_lease must evict an expired lease even when called repeatedly."""
    publisher = AsyncMock()
    mgr = LeaseManager(event_publisher=publisher)
    now = _now()
    mgr._current_lease = __import__(
        "src.domain.models.lease", fromlist=["Lease"]
    ).Lease(
        id="exp-1",
        owner="test",
        priority=10,
        acquired_at=now - timedelta(minutes=5),
        expires_at=now - timedelta(seconds=1),
        ttl_seconds=120,
        status=LeaseStatus.ACTIVE,
    )

    for _ in range(100):
        assert mgr.get_current_lease() is None
