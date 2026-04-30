"""Lease management API routes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.application.services.lease_manager import LeaseManager
from src.domain.exceptions.lease_exceptions import LeaseConflict, LeaseExpiredError, LeaseNotFound
from src.presentation.api.dependencies import get_lease_manager
from src.presentation.api.errors import error_payload, utc_now_iso
from src.presentation.api.schemas.lease_schema import (
    LeaseAcquireRequest,
    LeaseReleaseRequest,
    LeaseRenewRequest,
    LeaseResponse,
    LeaseStatusResponse,
)

router = APIRouter(prefix="/api/v1/lease", tags=["lease"])


def _correlation_id(request: Request) -> str | None:
    return getattr(request.state, "correlation_id", None)


@router.post(
    "/acquire",
    response_model=LeaseResponse,
    summary="Acquire an exclusive lease",
    responses={409: {"description": "A lease with equal or higher priority is already active."}},
)
async def acquire_lease(
    payload: LeaseAcquireRequest,
    request: Request,
    lease_manager: LeaseManager = Depends(get_lease_manager),
) -> LeaseResponse:
    """Request exclusive write control over the camera.

    If an active lease exists with **lower priority**, it is preempted and replaced
    by the new one. If the existing lease has **equal or higher priority**, HTTP 409
    is returned.
    """
    try:
        lease = await lease_manager.acquire(
            owner=payload.owner,
            priority=payload.priority,
            ttl_seconds=payload.ttl_seconds,
        )
    except LeaseConflict as exc:
        current = lease_manager.get_current_lease()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=error_payload(
                "LEASE_CONFLICT",
                str(exc),
                _correlation_id(request),
                {
                    "current_owner": current.owner if current else None,
                    "current_priority": current.priority if current else None,
                },
            ),
        ) from exc

    return LeaseResponse(
        lease_id=lease.id,
        owner=lease.owner,
        priority=lease.priority,
        acquired_at=lease.acquired_at,
        expires_at=lease.expires_at,
        permanent=lease.permanent,
    )


@router.post("/renew", summary="Renew an active lease")
async def renew_lease(
    payload: LeaseRenewRequest,
    request: Request,
    lease_manager: LeaseManager = Depends(get_lease_manager),
) -> dict[str, Any]:
    """Extend the TTL of an active lease from the current time.

    Must be called before the lease expires to avoid losing control of the camera.
    Returns HTTP 403 if the lease ID does not match or the lease has already expired.
    """
    try:
        lease = await lease_manager.renew(payload.lease_id, payload.ttl_seconds)
    except (LeaseNotFound, LeaseExpiredError) as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=error_payload("FORBIDDEN", str(exc), _correlation_id(request)),
        ) from exc
    return {"lease_id": lease.id, "expires_at": lease.expires_at}


@router.post("/release", summary="Release an active lease")
async def release_lease(
    payload: LeaseReleaseRequest,
    request: Request,
    lease_manager: LeaseManager = Depends(get_lease_manager),
) -> dict[str, Any]:
    """Explicitly release a lease, freeing the camera for other clients.

    Should always be called when the client no longer needs camera access,
    rather than waiting for the TTL to expire.
    """
    try:
        await lease_manager.release(payload.lease_id)
    except LeaseNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=error_payload("FORBIDDEN", str(exc), _correlation_id(request)),
        ) from exc
    return {"released": True, "released_at": utc_now_iso()}


@router.get("/status", response_model=LeaseStatusResponse, summary="Get current lease status")
async def lease_status(
    lease_manager: LeaseManager = Depends(get_lease_manager),
) -> LeaseStatusResponse:
    """Return the current lease status, including the holder, priority, and time remaining.

    Returns `active: false` when no lease is currently held.
    """
    lease = lease_manager.get_current_lease()
    if lease is None:
        return LeaseStatusResponse(active=False)
    expires_in_s = None if lease.permanent else max(
        0.0, (lease.expires_at - datetime.now(timezone.utc)).total_seconds()  # type: ignore[operator]
    )
    return LeaseStatusResponse(
        active=True,
        lease_id=lease.id,
        owner=lease.owner,
        priority=lease.priority,
        acquired_at=lease.acquired_at,
        expires_at=lease.expires_at,
        expires_in_s=expires_in_s,
    )
