"""Data transfer objects for lease operations."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class LeaseAcquireRequest(BaseModel):
    """Payload for lease acquisition requests."""

    owner: str = Field(min_length=1, max_length=100)
    priority: int = Field(ge=0, le=1000)
    ttl_seconds: int = Field(default=120, ge=10, le=600)


class LeaseRenewRequest(BaseModel):
    """Payload for lease renewal requests."""

    lease_id: str = Field(min_length=1)
    ttl_seconds: int = Field(default=120, ge=10, le=600)


class LeaseReleaseRequest(BaseModel):
    """Payload for lease release requests."""

    lease_id: str = Field(min_length=1)


class LeaseResponse(BaseModel):
    """Lease response payload returned by application services."""

    lease_id: str
    owner: str
    priority: int
    acquired_at: datetime
    expires_at: datetime


class LeaseStatusResponse(BaseModel):
    """Full lease status response."""

    active: bool
    lease_id: str | None = None
    owner: str | None = None
    priority: int | None = None
    acquired_at: datetime | None = None
    expires_at: datetime | None = None
    expires_in_s: float | None = None
