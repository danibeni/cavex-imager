"""Camera control and capture API routes."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from src.application.services.capture_service import CaptureService
from src.application.services.lease_manager import LeaseManager
from src.domain.exceptions.capture_exceptions import (
    ExposureInProgress,
    HardwareError,
    InvalidCaptureParameters,
    NoActiveExposure,
)
from src.domain.exceptions.lease_exceptions import LeaseNotFound
from src.domain.ports.camera_device import ICameraDevice
from src.domain.ports.storage import IStorage
from src.presentation.api.dependencies import (
    get_camera,
    get_capture_service,
    get_lease_manager,
    get_storage,
)
from src.presentation.api.errors import error_payload, utc_now_iso
from src.presentation.api.schemas.capture_schema import (
    ExposureStartRequest,
    ExposureStartResponse,
    SequenceStartRequest,
    SequenceStartResponse,
    SequenceStopRequest,
)
from src.shared.observability import disk_free_gb

router = APIRouter(prefix="/api/v1/camera", tags=["camera"])


class ConnectRequest(BaseModel):
    """Empty body for connect endpoint."""


class DisconnectRequest(BaseModel):
    """Empty body for disconnect endpoint."""


class ExposureAbortRequest(BaseModel):
    """Empty body for abort endpoint."""


class CameraConfigBinning(BaseModel):
    """Camera binning settings."""

    x: int = Field(ge=1, le=8)
    y: int = Field(ge=1, le=8)


class CameraConfigROI(BaseModel):
    """Camera region of interest settings."""

    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class CameraConfigCooler(BaseModel):
    """Camera cooler settings."""

    enabled: bool
    target_c: float


class CameraConfigRequest(BaseModel):
    """Camera acquisition configuration request."""

    binning: CameraConfigBinning | None = None
    roi: CameraConfigROI | None = None
    gain: int | None = None
    offset: int | None = None
    cooler: CameraConfigCooler | None = None


class SessionRequest(BaseModel):
    """Session storage configuration request."""

    storage_path: str
    file_prefix: str
    naming_pattern: str = "{prefix}_{seq:04d}.fits"


def _correlation_id(request: Request) -> str | None:
    return getattr(request.state, "correlation_id", None)


def _require_active_lease(lease_manager: LeaseManager, request: Request) -> str:
    lease = lease_manager.get_current_lease()
    if lease is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=error_payload("FORBIDDEN", "No active lease.", _correlation_id(request)),
        )
    return lease.id


@router.get("/status")
async def camera_status(
    request: Request,
    camera: ICameraDevice = Depends(get_camera),
    lease_manager: LeaseManager = Depends(get_lease_manager),
) -> dict:
    """Return complete camera status snapshot from local cache."""
    state = camera.get_state()
    lease = lease_manager.get_current_lease()
    now = datetime.now(timezone.utc)
    session_cfg = request.app.state.session_config
    return {
        "timestamp": now.isoformat(),
        "device": {
            "name": state.device_name,
            "model": "unknown",
            "interface": "INDIGO_INTERFACE_CCD",
            "connected": state.connected,
        },
        "indigo": {
            "connected": state.indigo_connected,
            "server": "configured",
        },
        "status": {
            "state": state.exposure_state,
            "exposure": {
                "is_exposing": state.exposure_state.upper() in {"BUSY", "ALERT"},
                "elapsed_s": state.exposure_value,
                "total_s": state.exposure_target,
                "progress_pct": (
                    (state.exposure_value / state.exposure_target) * 100.0
                    if state.exposure_target > 0
                    else 0.0
                ),
            },
            "thermal": {
                "ccd_temp_c": state.ccd_temp_c,
                "target_temp_c": state.cooler_target_c,
                "cooler_on": state.cooler_on,
                "cooler_power_pct": state.cooler_power_pct,
                "cooler_state": state.cooler_state,
            },
        },
        "config": {
            "binning": [state.bin_x, state.bin_y],
            "roi": list(state.roi),
            "gain": state.gain,
            "offset": state.offset,
        },
        "storage": {
            "session_path": session_cfg.get("storage_path"),
            "last_file": state.last_image_path,
            "disk_free_gb": session_cfg.get("disk_free_gb"),
        },
        "last_capture": request.app.state.last_capture_result,
        "lease": (
            {
                "active": True,
                "lease_id": lease.id,
                "owner": lease.owner,
                "priority": lease.priority,
                "acquired_at": lease.acquired_at.isoformat(),
                "expires_at": lease.expires_at.isoformat(),
                "expires_in_s": lease.remaining_seconds(),
            }
            if lease is not None
            else {"active": False}
        ),
    }


@router.get("/capabilities")
async def camera_capabilities(camera: ICameraDevice = Depends(get_camera)) -> dict:
    """Return detected camera capabilities."""
    state = camera.get_state()
    return {
        "device": {
            "name": state.device_name,
            "model": "unknown",
            "interface": "INDIGO_INTERFACE_CCD",
        },
        "sensor": {"width_px": 0, "height_px": 0, "pixel_size_um": 0.0, "bit_depth": 16},
        "binning": {"supported": [1, 2, 4], "max_x": 4, "max_y": 4},
        "gain": {"supported": True, "min": 0, "max": 100, "step": 1},
        "offset": {"supported": True, "min": 0, "max": 100, "step": 1},
        "cooler": {"supported": True, "min_temp_c": -50, "max_temp_c": 50},
        "features": {"can_abort": True, "has_shutter": False, "has_guide_port": False},
    }


@router.post("/connect")
async def camera_connect(
    _: ConnectRequest,
    request: Request,
    camera: ICameraDevice = Depends(get_camera),
    lease_manager: LeaseManager = Depends(get_lease_manager),
) -> dict:
    """Connect camera device."""
    _require_active_lease(lease_manager, request)
    try:
        await camera.connect_device()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=error_payload("INDIGO_UNAVAILABLE", str(exc), _correlation_id(request)),
        ) from exc
    state = camera.get_state()
    return {"device": state.device_name, "connected": True, "connected_at": utc_now_iso()}


@router.post("/disconnect")
async def camera_disconnect(
    _: DisconnectRequest,
    request: Request,
    camera: ICameraDevice = Depends(get_camera),
    lease_manager: LeaseManager = Depends(get_lease_manager),
) -> dict:
    """Disconnect camera device."""
    _require_active_lease(lease_manager, request)
    await camera.disconnect_device()
    state = camera.get_state()
    return {
        "device": state.device_name,
        "connected": False,
        "disconnected_at": utc_now_iso(),
    }


@router.post("/config")
async def camera_config(
    payload: CameraConfigRequest,
    request: Request,
    camera: ICameraDevice = Depends(get_camera),
    capture_service: CaptureService = Depends(get_capture_service),
    lease_manager: LeaseManager = Depends(get_lease_manager),
) -> dict:
    """Apply acquisition configuration."""
    _require_active_lease(lease_manager, request)
    current = capture_service.get_current_capture()
    if current is not None and current.status.value == "exposing":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=error_payload(
                "CONFLICT",
                "Cannot change config during exposure.",
                _correlation_id(request),
            ),
        )

    config: dict[str, object] = {}
    if payload.binning is not None:
        config["bin_x"] = payload.binning.x
        config["bin_y"] = payload.binning.y
    if payload.roi is not None:
        config["roi"] = (payload.roi.x, payload.roi.y, payload.roi.width, payload.roi.height)
    if payload.gain is not None:
        config["gain"] = payload.gain
    if payload.offset is not None:
        config["offset"] = payload.offset
    if payload.cooler is not None:
        config["cooler_enabled"] = payload.cooler.enabled
        config["cooler_target_c"] = payload.cooler.target_c

    try:
        await camera.set_config(config)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_payload("VALIDATION_ERROR", str(exc), _correlation_id(request)),
        ) from exc

    return {
        "applied": True,
        "config": {
            "binning": [config.get("bin_x"), config.get("bin_y")],
            "roi": list(config.get("roi", (0, 0, 0, 0))),
            "gain": config.get("gain"),
            "offset": config.get("offset"),
            "cooler_target_c": config.get("cooler_target_c"),
        },
        "applied_at": utc_now_iso(),
    }


@router.post("/session")
async def camera_session(
    payload: SessionRequest,
    request: Request,
    camera: ICameraDevice = Depends(get_camera),
    storage: IStorage = Depends(get_storage),
    lease_manager: LeaseManager = Depends(get_lease_manager),
) -> dict:
    """Configure camera local save mode and session storage."""
    _require_active_lease(lease_manager, request)
    validation = await storage.validate_path(payload.storage_path)
    if not validation.writable:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_payload(
                "VALIDATION_ERROR",
                validation.error or "Invalid storage path.",
                _correlation_id(request),
            ),
        )
    if not validation.valid:
        raise HTTPException(
            status_code=507,
            detail=error_payload(
                "IO_ERROR", validation.error or "Insufficient space.", _correlation_id(request)
            ),
        )

    await camera.set_local_mode(payload.storage_path, payload.file_prefix)
    request.app.state.session_config = {
        "session_id": f"session_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
        "storage_path": payload.storage_path,
        "file_prefix": payload.file_prefix,
        "naming_pattern": payload.naming_pattern,
        "disk_free_gb": round(validation.disk_free_gb, 3),
    }
    disk_free_gb.set(validation.disk_free_gb)
    return {
        "session_id": request.app.state.session_config["session_id"],
        "storage_path": payload.storage_path,
        "disk_free_gb": round(validation.disk_free_gb, 3),
        "configured_at": utc_now_iso(),
    }


@router.post(
    "/exposure/start", status_code=status.HTTP_202_ACCEPTED, response_model=ExposureStartResponse
)
async def exposure_start(
    payload: ExposureStartRequest,
    request: Request,
    capture_service: CaptureService = Depends(get_capture_service),
    lease_manager: LeaseManager = Depends(get_lease_manager),
) -> ExposureStartResponse:
    """Start single exposure."""
    lease_id = _require_active_lease(lease_manager, request)
    try:
        capture = await capture_service.start_exposure(
            lease_id=lease_id, exptime_s=payload.exptime_s, job_id=payload.job_id
        )
    except LeaseNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=error_payload("FORBIDDEN", str(exc), _correlation_id(request)),
        ) from exc
    except ExposureInProgress as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=error_payload("CONFLICT", str(exc), _correlation_id(request)),
        ) from exc
    except (InvalidCaptureParameters, HardwareError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_payload("VALIDATION_ERROR", str(exc), _correlation_id(request)),
        ) from exc

    started_at = capture.started_at or datetime.now(timezone.utc)
    return ExposureStartResponse(
        exposure_id=capture.id,
        exptime_s=payload.exptime_s,
        started_at=started_at,
        estimated_done_at=started_at + timedelta(seconds=payload.exptime_s),
    )


@router.post("/exposure/abort")
async def exposure_abort(
    _: ExposureAbortRequest,
    request: Request,
    capture_service: CaptureService = Depends(get_capture_service),
    lease_manager: LeaseManager = Depends(get_lease_manager),
) -> dict:
    """Abort active exposure."""
    lease_id = _require_active_lease(lease_manager, request)
    try:
        await capture_service.abort_exposure(lease_id)
    except NoActiveExposure as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=error_payload("CONFLICT", str(exc), _correlation_id(request)),
        ) from exc
    return {"aborted": True, "aborted_at": utc_now_iso()}


@router.post(
    "/sequence/start", status_code=status.HTTP_202_ACCEPTED, response_model=SequenceStartResponse
)
async def sequence_start(
    payload: SequenceStartRequest,
    request: Request,
    lease_manager: LeaseManager = Depends(get_lease_manager),
) -> SequenceStartResponse:
    """Start a scheduled sequence state machine."""
    _require_active_lease(lease_manager, request)
    if request.app.state.sequence_state["running"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=error_payload("CONFLICT", "Sequence already running.", _correlation_id(request)),
        )

    sequence_id = f"seq_{uuid4().hex[:8]}"
    request.app.state.sequence_state.update(
        {
            "running": True,
            "sequence_id": sequence_id,
            "count": payload.count,
            "completed_count": 0,
            "started_at": datetime.now(timezone.utc),
        }
    )
    request.app.state.sequence_task = asyncio.create_task(_sequence_counter(request, payload))
    return SequenceStartResponse(
        sequence_id=sequence_id,
        exptime_s=payload.exptime_s,
        count=payload.count,
        period_s=payload.period_s,
        started_at=request.app.state.sequence_state["started_at"],
    )


@router.post("/sequence/stop")
async def sequence_stop(payload: SequenceStopRequest, request: Request) -> dict:
    """Stop an active sequence."""
    state = request.app.state.sequence_state
    if not state["running"] or state["sequence_id"] != payload.sequence_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error_payload("NOT_FOUND", "Sequence not found.", _correlation_id(request)),
        )

    state["running"] = False
    task = request.app.state.sequence_task
    if task is not None:
        task.cancel()
    return {
        "stopped": True,
        "stopped_at": utc_now_iso(),
        "completed_count": state["completed_count"],
    }


async def _sequence_counter(request: Request, payload: SequenceStartRequest) -> None:
    """Lightweight sequence ticker used by presentation API."""
    state = request.app.state.sequence_state
    try:
        while state["running"]:
            await asyncio.sleep(payload.period_s)
            state["completed_count"] += 1
            if payload.count > 0 and state["completed_count"] >= payload.count:
                state["running"] = False
                break
    except asyncio.CancelledError:
        return
    finally:
        if not state["running"]:
            request.app.state.sequence_task = None
