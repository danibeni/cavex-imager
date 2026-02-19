"""FastAPI dependency providers backed by application state."""

from __future__ import annotations

from fastapi import Request

from src.application.services.capture_service import CaptureService
from src.application.services.lease_manager import LeaseManager
from src.domain.ports.camera_device import ICameraDevice
from src.domain.ports.event_publisher import IEventPublisher
from src.domain.ports.storage import IStorage
from src.domain.ports.telemetry import ITelemetryPublisher
from src.shared.config import Config


def get_config(request: Request) -> Config:
    """Return application configuration."""
    return request.app.state.config


def get_event_publisher(request: Request) -> IEventPublisher:
    """Return domain event publisher."""
    return request.app.state.event_publisher


def get_camera(request: Request) -> ICameraDevice:
    """Return camera device port implementation."""
    return request.app.state.camera


def get_storage(request: Request) -> IStorage:
    """Return storage port implementation."""
    return request.app.state.storage


def get_lease_manager(request: Request) -> LeaseManager:
    """Return lease manager service."""
    return request.app.state.lease_manager


def get_capture_service(request: Request) -> CaptureService:
    """Return capture orchestration service."""
    return request.app.state.capture_service


def get_telemetry_publisher(request: Request) -> ITelemetryPublisher:
    """Return telemetry publisher implementation."""
    return request.app.state.telemetry_publisher
