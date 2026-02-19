"""Unit tests for CaptureService."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.application.services.capture_service import CaptureService
from src.application.services.lease_manager import LeaseManager
from src.domain.exceptions.capture_exceptions import ExposureInProgress, NoActiveExposure
from src.domain.exceptions.lease_exceptions import LeaseNotFound
from src.domain.models.capture import CaptureStatus
from src.domain.models.lease import Lease, LeaseStatus
from src.domain.ports.camera_device import CameraStateSnapshot
from src.domain.ports.image_validator import IImageValidator


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _active_lease() -> Lease:
    """Create an active lease for service tests."""
    now = _now()
    return Lease(
        id="lease-1",
        owner="orchestrator",
        priority=100,
        acquired_at=now,
        expires_at=now + timedelta(minutes=5),
        ttl_seconds=300,
        status=LeaseStatus.ACTIVE,
    )


def _mock_camera_state() -> CameraStateSnapshot:
    """Create a mock camera state snapshot."""
    return CameraStateSnapshot(
        device_name="CAVEXCam",
        connected=True,
        indigo_connected=True,
        exposure_state="IDLE",
        exposure_value=0.0,
        exposure_target=0.0,
        ccd_temp_c=-10.0,
        cooler_target_c=-10.0,
        cooler_power_pct=45.0,
        cooler_on=True,
        cooler_state="STABLE",
        bin_x=1,
        bin_y=1,
        roi=(0, 0, 9600, 6422),
        gain=26,
        offset=10,
        last_image_path=None,
        local_mode_dir="/data/2026-02-12/",
        last_update=_now(),
    )


def _mock_validator(valid: bool = True, error: str | None = None) -> IImageValidator:
    """Create a mock image validator."""
    validator = MagicMock(spec=IImageValidator)
    validator.validate = MagicMock(return_value=(valid, error))
    return validator


def _build_service(
    lease: Lease | None = None,
    image_valid: bool = True,
) -> tuple[CaptureService, AsyncMock, AsyncMock, AsyncMock]:
    """Build CaptureService with mocked dependencies."""
    camera = MagicMock()
    camera.start_exposure = AsyncMock()
    camera.abort_exposure = AsyncMock()
    camera.get_state = MagicMock(return_value=_mock_camera_state())
    camera.subscribe_to_exposure_events = MagicMock()

    storage = AsyncMock()
    storage.write_sidecar = AsyncMock(return_value=True)

    publisher = AsyncMock()

    lease_manager = MagicMock(spec=LeaseManager)
    if lease:
        lease_manager.require_lease = MagicMock(return_value=lease)
        lease_manager.get_current_lease = MagicMock(return_value=lease)
    else:
        lease_manager.require_lease = MagicMock(side_effect=LeaseNotFound("No lease"))
        lease_manager.get_current_lease = MagicMock(return_value=None)

    error_msg = None if image_valid else "FITS validation error: corrupt"
    validator = _mock_validator(valid=image_valid, error=error_msg)

    service = CaptureService(camera, storage, publisher, lease_manager, validator)
    return service, camera, storage, publisher


def test_service_subscribes_to_camera_exposure_events() -> None:
    """Test service subscribes handlers to camera during initialization."""
    _, camera, _, _ = _build_service(lease=_active_lease())
    camera.subscribe_to_exposure_events.assert_called_once()


@pytest.mark.asyncio
async def test_start_exposure_creates_capture() -> None:
    """Test start_exposure creates and returns capture in exposing state."""
    service, camera, _, publisher = _build_service(lease=_active_lease())

    capture = await service.start_exposure(lease_id="lease-1", exptime_s=3.0, job_id="job_001")

    assert capture.status is CaptureStatus.EXPOSING
    assert capture.started_at is not None
    assert capture.job_id == "job_001"
    camera.start_exposure.assert_awaited_once_with(3.0)
    publisher.publish.assert_awaited()


@pytest.mark.asyncio
async def test_start_exposure_raises_if_no_lease() -> None:
    """Test start_exposure raises LeaseNotFound when no active lease."""
    service, _, _, _ = _build_service(lease=None)

    with pytest.raises(LeaseNotFound):
        await service.start_exposure(lease_id="missing", exptime_s=3.0)


@pytest.mark.asyncio
async def test_start_exposure_raises_if_already_exposing() -> None:
    """Test start_exposure raises ExposureInProgress when already exposing."""
    service, _, _, _ = _build_service(lease=_active_lease())
    await service.start_exposure(lease_id="lease-1", exptime_s=3.0)

    with pytest.raises(ExposureInProgress):
        await service.start_exposure(lease_id="lease-1", exptime_s=3.0)


@pytest.mark.asyncio
async def test_handle_exposure_complete() -> None:
    """Test handle_exposure_complete marks capture as completed."""
    service, _, storage, publisher = _build_service(lease=_active_lease())
    await service.start_exposure(lease_id="lease-1", exptime_s=3.0)

    await service.handle_exposure_complete("/data/2026-02-12/cavex_0042.fits")

    capture = service.get_current_capture()
    assert capture is not None
    assert capture.status is CaptureStatus.COMPLETED
    assert capture.file_path == "/data/2026-02-12/cavex_0042.fits"
    assert capture.sidecar_written is True
    storage.write_sidecar.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_exposure_complete_with_sidecar_failure() -> None:
    """Test handle_exposure_complete when sidecar write fails."""
    service, _, storage, _ = _build_service(lease=_active_lease())
    storage.write_sidecar.return_value = False
    await service.start_exposure(lease_id="lease-1", exptime_s=3.0)

    await service.handle_exposure_complete("/data/test.fits")

    capture = service.get_current_capture()
    assert capture is not None
    assert capture.status is CaptureStatus.COMPLETED
    assert capture.sidecar_written is False


@pytest.mark.asyncio
async def test_handle_exposure_failed() -> None:
    """Test handle_exposure_failed marks capture as failed."""
    service, _, _, publisher = _build_service(lease=_active_lease())
    await service.start_exposure(lease_id="lease-1", exptime_s=3.0)

    await service.handle_exposure_failed("INDIGO timeout")

    capture = service.get_current_capture()
    assert capture is not None
    assert capture.status is CaptureStatus.FAILED
    assert capture.error_message == "INDIGO timeout"


@pytest.mark.asyncio
async def test_abort_exposure() -> None:
    """Test abort_exposure aborts camera and updates state."""
    service, camera, _, publisher = _build_service(lease=_active_lease())
    await service.start_exposure(lease_id="lease-1", exptime_s=10.0)

    await service.abort_exposure(lease_id="lease-1")

    camera.abort_exposure.assert_awaited_once()
    capture = service.get_current_capture()
    assert capture is not None
    assert capture.status is CaptureStatus.ABORTED


@pytest.mark.asyncio
async def test_abort_exposure_raises_if_no_active() -> None:
    """Test abort raises NoActiveExposure when nothing is running."""
    service, _, _, _ = _build_service(lease=_active_lease())

    with pytest.raises(NoActiveExposure):
        await service.abort_exposure(lease_id="lease-1")


@pytest.mark.asyncio
async def test_state_ok_then_image_completes_capture(tmp_path: Path) -> None:
    """Test reactive flow completes capture when OK and image are available."""
    service, _, storage, _ = _build_service(lease=_active_lease())
    await service.start_exposure(lease_id="lease-1", exptime_s=1.0)
    image_path = tmp_path / "capture_001.fits"
    image_path.write_text("fits-placeholder", encoding="utf-8")

    await service._on_exposure_state_changed("Busy", "Ok")
    capture_mid = service.get_current_capture()
    assert capture_mid is not None
    assert capture_mid.status is CaptureStatus.EXPOSING

    await service._on_image_received(str(image_path))

    capture = service.get_current_capture()
    assert capture is not None
    assert capture.status is CaptureStatus.COMPLETED
    assert capture.file_path == str(image_path)
    storage.write_sidecar.assert_awaited_once()


@pytest.mark.asyncio
async def test_state_alert_marks_capture_failed() -> None:
    """Test ALERT state transitions capture to failed."""
    service, _, _, _ = _build_service(lease=_active_lease())
    await service.start_exposure(lease_id="lease-1", exptime_s=1.0)

    await service._on_exposure_state_changed("Busy", "Alert")

    capture = service.get_current_capture()
    assert capture is not None
    assert capture.status is CaptureStatus.FAILED


@pytest.mark.asyncio
async def test_handle_exposure_complete_publishes_image_valid_true(tmp_path: Path) -> None:
    """Test capture.completed event includes image_valid=True when validator passes."""
    service, _, _, publisher = _build_service(lease=_active_lease(), image_valid=True)
    await service.start_exposure(lease_id="lease-1", exptime_s=1.0)

    fits_path = tmp_path / "image.fits"
    fits_path.write_text("placeholder", encoding="utf-8")
    await service.handle_exposure_complete(str(fits_path))

    published = publisher.publish.call_args_list
    completed_call = next(c for c in published if c.args[0] == "capture.completed")
    payload = completed_call.args[1]
    assert payload["image_valid"] is True
    assert payload["image_error"] is None


@pytest.mark.asyncio
async def test_handle_exposure_complete_publishes_image_valid_false(tmp_path: Path) -> None:
    """Test capture.completed event includes image_valid=False when validator fails."""
    service, _, _, publisher = _build_service(lease=_active_lease(), image_valid=False)
    await service.start_exposure(lease_id="lease-1", exptime_s=1.0)

    fits_path = tmp_path / "image.fits"
    fits_path.write_text("corrupt", encoding="utf-8")
    await service.handle_exposure_complete(str(fits_path))

    published = publisher.publish.call_args_list
    completed_call = next(c for c in published if c.args[0] == "capture.completed")
    payload = completed_call.args[1]
    assert payload["image_valid"] is False
    assert payload["image_error"] is not None


@pytest.mark.asyncio
async def test_on_image_received_missing_file_fails_capture() -> None:
    """Test that a missing image file triggers handle_exposure_failed."""
    service, _, _, publisher = _build_service(lease=_active_lease())
    await service.start_exposure(lease_id="lease-1", exptime_s=1.0)

    await service._on_exposure_state_changed("Busy", "Ok")
    await service._on_image_received("/tmp/cavex_nonexistent_test.fits")

    capture = service.get_current_capture()
    assert capture is not None
    assert capture.status is CaptureStatus.FAILED
    published_events = [c.args[0] for c in publisher.publish.call_args_list]
    assert "capture.failed" in published_events
