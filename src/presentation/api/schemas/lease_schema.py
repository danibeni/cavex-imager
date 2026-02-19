"""Lease API schemas.

Examples:
    `LeaseAcquireRequest(owner="cavex_manager", priority=100, ttl_seconds=120)`
    `LeaseRenewRequest(lease_id="lease_a1b2c3d4", ttl_seconds=120)`
    `LeaseReleaseRequest(lease_id="lease_a1b2c3d4")`
"""

from src.application.dtos.lease_dto import (
    LeaseAcquireRequest,
    LeaseReleaseRequest,
    LeaseRenewRequest,
    LeaseResponse,
    LeaseStatusResponse,
)

__all__ = [
    "LeaseAcquireRequest",
    "LeaseRenewRequest",
    "LeaseReleaseRequest",
    "LeaseResponse",
    "LeaseStatusResponse",
]
