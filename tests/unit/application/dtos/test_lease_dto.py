"""Unit tests for lease DTOs."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.application.dtos.lease_dto import LeaseAcquireRequest, LeaseRenewRequest


def test_acquire_request_validates_with_valid_values() -> None:
    """Test LeaseAcquireRequest validation with valid values."""
    dto = LeaseAcquireRequest(owner="orchestrator", priority=100, ttl_seconds=300)
    assert dto.owner == "orchestrator"
    assert dto.priority == 100
    assert dto.ttl_seconds == 300


def test_acquire_request_uses_defaults() -> None:
    """Test LeaseAcquireRequest uses default ttl."""
    dto = LeaseAcquireRequest(owner="test", priority=50)
    assert dto.ttl_seconds == 120


@pytest.mark.parametrize("ttl", [9, 601])
def test_acquire_request_fails_when_ttl_out_of_range(ttl: int) -> None:
    """Test LeaseAcquireRequest fails for TTL outside allowed range."""
    with pytest.raises(ValidationError):
        LeaseAcquireRequest(owner="orchestrator", priority=100, ttl_seconds=ttl)


def test_acquire_request_fails_with_negative_priority() -> None:
    """Test LeaseAcquireRequest fails with negative priority."""
    with pytest.raises(ValidationError):
        LeaseAcquireRequest(owner="test", priority=-1, ttl_seconds=120)


def test_renew_request_validates_correctly() -> None:
    """Test LeaseRenewRequest validation."""
    dto = LeaseRenewRequest(lease_id="lease-123", ttl_seconds=300)
    assert dto.lease_id == "lease-123"
    assert dto.ttl_seconds == 300
