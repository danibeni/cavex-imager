"""Unit tests for CaptureService."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest

from src.application.services.capture_service import CaptureService
from src.application.services.lease_manager import LeaseManager
from src.domain.exceptions.capture_exceptions import ExposureInProgress, NoActiveExposure
from src.domain.exceptions.lease_exceptions import LeaseNotFound
from src.domain.models.capture import Capture, CaptureStatus, FrameType
from src.domain.models.lease import Lease, LeaseStatus
from src.domain.ports.camera_device import CameraStateSnapshot


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


def _build_service(
    lease: Lease | None = None,
) -> tuple[CaptureService, AsyncMock, AsyncMock, AsyncMock]:
    """Build CaptureService with mocked dependencies."""
    camera = MagicMock()
    camera.start_exposure = AsyncMock()
    camera.abort_exposure = AsyncMock()
    camera.get_state = MagicMock(return_value=_mock_camera_state())

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

    service = CaptureService(camera, storage, publisher, lease_manager)
    return service, camera, storage, publisher


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
