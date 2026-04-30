"""CAVEX Imager FastAPI application entrypoint."""

from __future__ import annotations
import os
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from src.presentation.api.routes.preview import router as preview_router
from src.application.services.capture_service import CaptureService
from src.application.services.lease_manager import LeaseManager
from src.infrastructure.adapters.indigo_camera_adapter import IndigoCameraAdapter
from src.infrastructure.bus.internal_event_bus import InternalEventBus
from src.infrastructure.persistence.sidecar_writer import SidecarWriter
from src.infrastructure.validation.fits_validator import FitsValidator
from src.presentation.api.auth import is_valid_api_key
from src.presentation.api.errors import error_payload
from src.presentation.api.routes.camera import router as camera_router
from src.presentation.api.routes.health import router as health_router
from src.presentation.api.routes.lease import router as lease_router
from src.presentation.api.routes.telemetry import router as telemetry_router
from src.presentation.websocket.telemetry_handler import TelemetryHandler
from src.shared.config import Config
from src.shared.logging import bind_correlation_id, clear_correlation_id, setup_logging
from src.shared.observability import (
    captures_failed_total,
    captures_total,
    device_connected,
    indigo_connected,
)

logger = structlog.get_logger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle."""
    config = Config()
    setup_logging(
        level=os.getenv("LOG_LEVEL", config.get("service.log_level", "INFO")),
        file_enabled=config.get("logging.file.enabled", True),
        file_path=config.get("logging.file.path", "/data/logs/cavex_imager.log"),
        max_bytes=int(config.get("logging.file.max_bytes", 10_485_760)),
        backup_count=int(config.get("logging.file.backup_count", 5)),
    )

    event_bus = InternalEventBus()
    storage = SidecarWriter(min_free_gb=float(config.get("storage.min_free_gb", 10.0)))
    camera = IndigoCameraAdapter(
        device_name=str(config.get("indigo.device_name", "CCD Imager Simulator"))
    )
    lease_manager = LeaseManager(
        event_publisher=event_bus,
        default_ttl=int(config.get("lease.default_ttl_seconds", 120)),
        max_ttl=int(config.get("lease.max_ttl_seconds", 600)),
        preempt_mode=str(config.get("lease.preempt_mode", "graceful")),
        graceful_timeout_s=int(config.get("lease.graceful_timeout_s", 60)),
    )
    capture_service = CaptureService(
        camera=camera,
        storage=storage,
        event_publisher=event_bus,
        lease_manager=lease_manager,
        image_validator=FitsValidator(),
        service_version=str(config.get("service.version", "1.0.0")),
    )
    lease_manager.set_exposing_check(capture_service.is_exposing)
    session_cfg: dict = {
        "session_id": None,
        "storage_path": config.get("storage.host_base_path", "/opt/cavex/data"),
        "file_prefix": config.get("storage.default_file_prefix", "cavex"),
        "naming_pattern": config.get("storage.default_naming_pattern", "{prefix}_{seq:04d}.fits"),
        "disk_free_gb": 0.0,
        "last_file": None,
    }

    telemetry = TelemetryHandler(
        camera=camera,
        max_clients=int(config.get("telemetry.max_ws_clients", 10)),
        lease_manager=lease_manager,
        session_config=session_cfg,
    )

    app.state.config = config
    app.state.event_publisher = event_bus
    app.state.storage = storage
    app.state.camera = camera
    app.state.lease_manager = lease_manager
    app.state.capture_service = capture_service
    app.state.telemetry_publisher = telemetry
    app.state.started_at = _utc_now()
    app.state.last_capture_status = "NONE"
    app.state.last_capture_result: dict | None = None
    app.state.active_alerts = []
    app.state.last_errors = []
    app.state.metrics_cache = {
        "captures_total": 0,
        "captures_failed": 0,
        "last_capture_duration_s": 0.0,
        "avg_capture_duration_s": 0.0,
    }
    app.state.session_config = session_cfg
    app.state.sequence_state = {
        "running": False,
        "sequence_id": None,
        "count": 0,
        "completed_count": 0,
        "started_at": None,
    }
    app.state.sequence_task = None
    app.state.telemetry_task = None

    async def on_capture_started(payload: dict[str, Any]) -> None:
        app.state.last_capture_status = "RUNNING"
        captures_total.labels(status="started").inc()
        await telemetry.publish_event(
            "capture_event",
            {
                "event": "capture_started",
                "exposure_id": payload.get("capture_id"),
                "job_id": payload.get("job_id"),
                "exptime_s": payload.get("exptime_s"),
            },
        )

    async def on_capture_completed(payload: dict[str, Any]) -> None:
        app.state.last_capture_status = "SUCCESS"
        app.state.metrics_cache["captures_total"] += 1
        captures_total.labels(status="completed").inc()
        if payload.get("file_path"):
            session_cfg["last_file"] = payload["file_path"].split("/")[-1]
        app.state.last_capture_result = {
            "capture_id": payload.get("capture_id"),
            "job_id": payload.get("job_id"),
            "exptime_s": payload.get("exptime_s"),
            "file_path": payload.get("file_path"),
            "sidecar_path": payload.get("sidecar_path"),
            "image_valid": payload.get("image_valid"),
            "image_error": payload.get("image_error"),
            "sidecar_written": payload.get("sidecar_written"),
            "ccd_temp_c": payload.get("ccd_temp_c"),
            "completed_at": _utc_now().isoformat(),
        }
        await telemetry.publish_event(
            "capture_event",
            {
                "event": "capture_done",
                "exposure_id": payload.get("capture_id"),
                "job_id": payload.get("job_id"),
                "exptime_s": payload.get("exptime_s"),
                "image_path": payload.get("file_path"),
                "sidecar_path": payload.get("sidecar_path"),
                "image_valid": payload.get("image_valid"),
                "image_error": payload.get("image_error"),
                "sidecar_written": payload.get("sidecar_written"),
                "ccd_temp_c": payload.get("ccd_temp_c"),
            },
        )

    async def on_capture_failed(payload: dict[str, Any]) -> None:
        app.state.last_capture_status = "FAILED"
        app.state.metrics_cache["captures_failed"] += 1
        captures_failed_total.inc()
        await telemetry.publish_event(
            "capture_event",
            {
                "event": "capture_failed",
                "exposure_id": payload.get("capture_id"),
                "message": payload.get("error_message"),
            },
        )

    async def on_capture_aborted(payload: dict[str, Any]) -> None:
        app.state.last_capture_status = "ABORTED"
        await telemetry.publish_event(
            "capture_event",
            {"event": "capture_aborted", "exposure_id": payload.get("capture_id")},
        )

    async def on_lease_event(event_name: str, payload: dict[str, Any]) -> None:
        await telemetry.publish_event("lease_event", {"event": event_name, **payload})

    await event_bus.subscribe("capture.started", on_capture_started)
    await event_bus.subscribe("capture.completed", on_capture_completed)
    await event_bus.subscribe("capture.failed", on_capture_failed)
    await event_bus.subscribe("capture.aborted", on_capture_aborted)
    await event_bus.subscribe(
        "lease.acquired", lambda payload: on_lease_event("lease_acquired", payload)
    )
    await event_bus.subscribe(
        "lease.renewed", lambda payload: on_lease_event("lease_renewed", payload)
    )
    await event_bus.subscribe(
        "lease.preempted", lambda payload: on_lease_event("lease_preempted", payload)
    )
    await event_bus.subscribe(
        "lease.released", lambda payload: on_lease_event("lease_released", payload)
    )

    data_dir = str(config.get("storage.host_base_path", "/opt/cavex/data"))
    os.makedirs(data_dir, exist_ok=True)

    indigo_host = os.getenv("INDIGO_HOST", config.get("indigo.host", "localhost"))
    indigo_port = int(os.getenv("INDIGO_PORT", config.get("indigo.port", 7624)))
    try:
        storage_path = str(config.get("storage.host_base_path", "/opt/cavex/data"))
        file_prefix = str(config.get("storage.default_file_prefix", "cavex"))
        await camera.connect(indigo_host, indigo_port)
        await camera.set_local_mode(directory=storage_path, prefix=file_prefix)

        indigo_connected.set(1)
        device_connected.set(1 if camera.get_state().connected else 0)
        logger.info("camera_local_mode_configured", directory=storage_path, prefix=file_prefix)
    except Exception as exc:
        indigo_connected.set(0)
        device_connected.set(0)
        logger.warning(
            "indigo_server_unavailable",
            host=indigo_host,
            port=indigo_port,
            error=str(exc),
        )

    async def telemetry_loop() -> None:
        while True:
            await telemetry.broadcast_snapshot()
            await asyncio.sleep(0.5)

    app.state.telemetry_task = asyncio.create_task(telemetry_loop())
    logger.info("cavex_imager_ready")

    try:
        yield
    finally:
        if app.state.sequence_task is not None:
            app.state.sequence_task.cancel()
        if app.state.telemetry_task is not None:
            app.state.telemetry_task.cancel()
            try:
                await app.state.telemetry_task
            except asyncio.CancelledError:
                pass
        await camera.disconnect()
        logger.info("cavex_imager_stopped")


_DESCRIPTION = """
## CAVEX Imager API

REST/WebSocket service for controlling the reference astronomical camera of the
**CAVEX** instrument (Calar Alto V-band EXtinction monitor).

Provides an abstraction layer over the INDIGO protocol for image acquisition,
concurrent access management (lease system), and real-time telemetry streaming.

### Typical usage flow

1. **`POST /api/v1/lease/acquire`** — Acquire an exclusive write lease on the camera.
2. **`POST /api/v1/camera/connect`** — Connect the camera device via INDIGO.
3. **`POST /api/v1/camera/config`** — Configure acquisition parameters (binning, ROI, gain, cooler).
4. **`POST /api/v1/camera/exposure/start`** — Start an exposure (HTTP 202 Accepted, async operation).
5. **`WS  /api/v1/ws/telemetry`** — Subscribe to real-time telemetry to monitor progress.
6. **`POST /api/v1/lease/release`** — Release the lease when done.

### Authentication

When authentication is enabled (`auth.enabled: true` in `config/default.yaml`),
all routes under `/api/v1/` require the `X-API-Key` header with a valid key.

### WebSocket telemetry

The `/api/v1/ws/telemetry` endpoint accepts a `rate_hz` query parameter (1 or 2)
to control the update frequency. Clients may send
`{"action": "subscribe", "topics": [...]}` for selective topic subscription.
"""

_OPENAPI_TAGS = [
    {
        "name": "health",
        "description": "Service health and operational diagnostics.",
    },
    {
        "name": "lease",
        "description": (
            "Exclusive lease management. "
            "Ensures only one client holds write control over the camera at any given time. "
            "Supports priority-based preemption and configurable TTLs."
        ),
    },
    {
        "name": "camera",
        "description": (
            "Camera device control: connection management, acquisition parameter configuration, "
            "single exposures, and periodic sequences."
        ),
    },
    {
        "name": "telemetry",
        "description": "Real-time telemetry WebSocket endpoint (up to 2 Hz).",
    },
    {
        "name": "preview",
        "description": "FITS image preview rendered as PNG with automatic contrast stretching.",
    },
]

app = FastAPI(
    title="CAVEX Imager",
    version="1.0.0",
    description=_DESCRIPTION,
    contact={
        "name": "Calar Alto Observatory — CAVEX Project",
        "url": "https://www.caha.es",
    },
    license_info={"name": "MIT"},
    openapi_tags=_OPENAPI_TAGS,
    lifespan=lifespan,
)


def _custom_openapi() -> dict:
    """Return the OpenAPI schema, injecting the API key security scheme."""
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        contact=app.contact,
        license_info=app.license_info,
        tags=app.openapi_tags,
        routes=app.routes,
    )
    schema.setdefault("components", {})["securitySchemes"] = {
        "ApiKeyAuth": {
            "type": "apiKey",
            "in": "header",
            "name": "X-API-Key",
            "description": (
                "API key for authentication. "
                "Required when `auth.enabled: true` in the service configuration."
            ),
        }
    }
    schema["security"] = [{"ApiKeyAuth": []}]
    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = _custom_openapi

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    """Attach a per-request correlation id to context and response."""
    correlation_id = request.headers.get("x-correlation-id") or f"corr_{_utc_now().timestamp():.6f}"
    request.state.correlation_id = correlation_id
    bind_correlation_id(correlation_id)
    try:
        response = await call_next(request)
    finally:
        clear_correlation_id()
    response.headers["X-Correlation-ID"] = correlation_id
    return response


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Validate API key when auth is enabled."""
    if request.url.path.startswith("/api/v1"):
        config: Config = request.app.state.config
        api_key = request.headers.get("x-api-key")
        if not is_valid_api_key(config, api_key):
            payload = error_payload(
                "UNAUTHORIZED",
                "Invalid API key.",
                getattr(request.state, "correlation_id", None),
            )
            return JSONResponse(status_code=401, content=payload)
    return await call_next(request)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Return spec-compliant error envelope for HTTP exceptions."""
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        payload = exc.detail
        payload["error"]["correlation_id"] = payload["error"].get("correlation_id") or getattr(
            request.state, "correlation_id", None
        )
        payload["error"]["timestamp"] = payload["error"].get("timestamp") or _utc_now().isoformat()
    else:
        payload = error_payload(
            "INTERNAL_ERROR",
            str(exc.detail),
            getattr(request.state, "correlation_id", None),
        )

    request.app.state.last_errors.append(payload["error"])
    return JSONResponse(status_code=exc.status_code, content=payload)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Return generic internal error in spec format."""
    logger.exception("unhandled_exception", error=str(exc))
    payload = error_payload(
        "INTERNAL_ERROR",
        "Unexpected internal error.",
        getattr(request.state, "correlation_id", None),
    )
    request.app.state.last_errors.append(payload["error"])
    return JSONResponse(status_code=500, content=payload)

app.mount("/captures", StaticFiles(directory="/opt/cavex/data"), name="data")

app.include_router(health_router)
app.include_router(lease_router)
app.include_router(camera_router)
app.include_router(telemetry_router)
app.include_router(preview_router)


@app.get("/")
async def root() -> dict[str, str]:
    """Return service identity and operational state."""
    return {"service": "cavex_imager", "version": "1.0.0", "status": "operational"}
