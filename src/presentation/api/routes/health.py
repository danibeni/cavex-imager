"""Health and diagnostics API routes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse
from prometheus_client import generate_latest

from src.domain.ports.camera_device import ICameraDevice
from src.domain.ports.storage import IStorage
from src.domain.ports.telemetry import ITelemetryPublisher
from src.presentation.api.dependencies import (
    get_camera,
    get_config,
    get_storage,
    get_telemetry_publisher,
)
from src.shared.config import Config

router = APIRouter(prefix="/api/v1", tags=["health"])


def _uptime_seconds(request: Request) -> float:
    started_at: datetime = request.app.state.started_at
    return max(0.0, (datetime.now(timezone.utc) - started_at).total_seconds())


@router.get(
    "/health",
    responses={
        200: {
            "description": "Service health status",
            "content": {
                "application/json": {
                    "example": {
                        "status": "OK",
                        "uptime_s": 86400,
                        "checks": {
                            "indigo_server": "CONNECTED",
                            "camera_device": "READY",
                            "storage_writable": "YES",
                            "last_exposure": "SUCCESS",
                            "active_alerts": 0,
                        },
                        "summary": "Camera CAVEXCam is idle at -10.2C. Storage has 450GB free.",
                    }
                }
            },
        }
    },
)
async def get_health(
    request: Request,
    camera: ICameraDevice = Depends(get_camera),
    storage: IStorage = Depends(get_storage),
    config: Config = Depends(get_config),
) -> dict[str, Any]:
    """Return service health with deterministic top-level fields."""
    state = camera.get_state()
    validation = await storage.validate_path(
        state.local_mode_dir or config.get("storage.host_base_path")
    )
    last_capture_status = request.app.state.last_capture_status
    active_alerts = len(request.app.state.active_alerts)

    checks = {
        "indigo_server": "CONNECTED" if state.indigo_connected else "DISCONNECTED",
        "camera_device": "READY" if state.connected else "NOT_READY",
        "storage_writable": "YES" if validation.writable else "NO",
        "last_exposure": last_capture_status,
        "active_alerts": active_alerts,
    }

    status = "OK"
    if not state.indigo_connected or not validation.valid:
        status = "WARNING"
    if not validation.writable:
        status = "ERROR"

    return {
        "status": status,
        "uptime_s": round(_uptime_seconds(request), 3),
        "checks": checks,
        "summary": (
            f"Camera {state.device_name} is {state.exposure_state.lower()} at "
            f"{state.ccd_temp_c:.1f}C. Storage has {validation.disk_free_gb:.1f}GB free."
        ),
    }


@router.get("/diag/summary")
async def get_diag_summary(
    request: Request,
    camera: ICameraDevice = Depends(get_camera),
    telemetry: ITelemetryPublisher = Depends(get_telemetry_publisher),
    config: Config = Depends(get_config),
) -> dict[str, Any]:
    """Return concise technical diagnostic summary."""
    state = camera.get_state()
    return {
        "service": {
            "name": config.get("service.name"),
            "version": config.get("service.version"),
            "uptime_s": round(_uptime_seconds(request), 3),
            "started_at": request.app.state.started_at.isoformat(),
        },
        "indigo": {
            "connected": state.indigo_connected,
            "server_host": config.get("indigo.host"),
            "server_port": config.get("indigo.port"),
            "last_reconnect": None,
            "reconnect_attempts": 0,
        },
        "device": {
            "name": state.device_name,
            "model": "unknown",
            "connected": state.connected,
            "capabilities_detected": True,
        },
        "statistics": {
            "captures_total": request.app.state.metrics_cache["captures_total"],
            "captures_failed": request.app.state.metrics_cache["captures_failed"],
            "last_capture_duration_s": request.app.state.metrics_cache["last_capture_duration_s"],
            "avg_capture_duration_s": request.app.state.metrics_cache["avg_capture_duration_s"],
        },
        "websocket": {"clients_connected": telemetry.client_count()},
        "storage": {
            "session_path": request.app.state.session_config["storage_path"],
            "disk_free_gb": request.app.state.session_config["disk_free_gb"],
        },
    }


@router.get("/diag/last_errors")
async def get_diag_last_errors(request: Request) -> dict[str, Any]:
    """Return in-memory list of latest structured errors."""
    return {"errors": request.app.state.last_errors[-50:]}


@router.get("/diag/metrics", response_class=PlainTextResponse)
async def get_diag_metrics() -> PlainTextResponse:
    """Return Prometheus metrics text format."""
    return PlainTextResponse(generate_latest().decode("utf-8"), media_type="text/plain")
