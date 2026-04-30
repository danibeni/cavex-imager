"""Data transfer objects for lease operations."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class LeaseAcquireRequest(BaseModel):
    """Payload for requesting an exclusive write lease on the camera."""

    owner: str = Field(
        min_length=1,
        max_length=100,
        description="Identifier of the client requesting the lease.",
        examples=["cavex_manager"],
    )
    priority: int = Field(
        ge=0,
        le=1000,
        description=(
            "Lease priority (0–1000). "
            "A higher-priority client can preempt an active lease held by a lower-priority one."
        ),
        examples=[100],
    )
    ttl_seconds: int | None = Field(
        default=120,
        ge=10,
        le=600,
        description=(
            "Lease time-to-live in seconds (10–600). "
            "Use null for a permanent lease (no expiration) — "
            "the lease remains active until explicitly released."
        ),
        examples=[120],
    )


class LeaseRenewRequest(BaseModel):
    """Payload for renewing an active lease TTL."""

    lease_id: str = Field(
        min_length=1,
        description="Identifier of the lease to renew.",
        examples=["lease_a1b2c3d4"],
    )
    ttl_seconds: int = Field(
        default=120,
        ge=10,
        le=600,
        description="New time-to-live from now in seconds.",
        examples=[120],
    )


class LeaseReleaseRequest(BaseModel):
    """Payload for releasing an active lease."""

    lease_id: str = Field(
        min_length=1,
        description="Identifier of the lease to release.",
        examples=["lease_a1b2c3d4"],
    )


class LeaseResponse(BaseModel):
    """Response returned after acquiring or renewing a lease."""

    lease_id: str = Field(description="Unique lease identifier.", examples=["lease_a1b2c3d4"])
    owner: str = Field(description="Identifier of the lease holder.", examples=["cavex_manager"])
    priority: int = Field(description="Assigned priority.", examples=[100])
    acquired_at: datetime = Field(description="Acquisition timestamp (UTC ISO-8601).")
    expires_at: datetime | None = Field(
        description="Expiration timestamp (UTC ISO-8601). Null for permanent leases."
    )
    permanent: bool = Field(default=False, description="True if the lease has no expiration.")


class LeaseStatusResponse(BaseModel):
    """Current lease status in the system."""

    active: bool = Field(description="`true` if a lease is currently active.")
    lease_id: str | None = Field(default=None, description="Active lease identifier.", examples=["lease_a1b2c3d4"])
    owner: str | None = Field(default=None, description="Active lease holder.", examples=["cavex_manager"])
    priority: int | None = Field(default=None, description="Active lease priority.", examples=[100])
    acquired_at: datetime | None = Field(default=None, description="Acquisition timestamp (UTC ISO-8601).")
    expires_at: datetime | None = Field(default=None, description="Expiration timestamp (UTC ISO-8601).")
    expires_in_s: float | None = Field(default=None, description="Seconds remaining until expiration.", examples=[87.3])
