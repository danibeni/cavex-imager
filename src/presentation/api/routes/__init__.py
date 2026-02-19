"""API routes package."""

from src.presentation.api.routes.camera import router as camera_router
from src.presentation.api.routes.health import router as health_router
from src.presentation.api.routes.lease import router as lease_router
from src.presentation.api.routes.telemetry import router as telemetry_router

__all__ = ["health_router", "lease_router", "camera_router", "telemetry_router"]
