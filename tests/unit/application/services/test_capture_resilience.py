"""Resilience tests for CaptureService under error and edge-case conditions."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.application.services.capture_service import CaptureService
from src.application.services.lease_manager import LeaseManager
from src.domain.exceptions.capture_exceptions import ExposureInProgress, HardwareError, NoActiveExposure
from src.domain.exceptions.lease_exceptions import LeaseNotFound
from src.domain.models.capture import CaptureStatus
from src.domain.models.lease import Lease, LeaseStatus
from src.domain.ports.camera_device import CameraStateSnapshot
from src.domain.ports.image_validator import IImageValidator


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _active_lease() -> Lease:
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


def _camera_state() -> CameraStateSnapshot:
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
        local_mode_dir="/data/",
        last_update=_now(),
    )


def _build_service(lease: Lease | None = None) -> tuple[CaptureService, MagicMock]:
    camera = MagicMock()
    camera.start_exposure = AsyncMock()
    camera.abort_exposure = AsyncMock()
    camera.get_state = MagicMock(return_value=_camera_state())
    camera.subscribe_to_exposure_events = MagicMock()

    publisher = AsyncMock()
    validator = MagicMock(spec=IImageValidator)
    validator.validate = MagicMock(return_value=(True, None))

    lease_manager = MagicMock(spec=LeaseManager)
    if lease is None:
        lease_manager.require_lease.side_effect = LeaseNotFound("No active lease.")
        lease_manager.get_current_lease.return_value = None
    else:
        lease_manager.require_lease.return_value = lease
        lease_manager.get_current_lease.return_value = lease

    svc = CaptureService(
        camera=camera,
        storage=AsyncMock(),
        event_publisher=publisher,
        lease_manager=lease_manager,
        image_validator=validator,
    )
    return svc, camera


# ---------------------------------------------------------------------------
# Lease validation resilience
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_exposure_rejected_without_lease() -> None:
    """start_exposure must raise LeaseNotFound when no lease is active."""
    svc, _ = _build_service(lease=None)

    with pytest.raises(LeaseNotFound):
        await svc.start_exposure(lease_id="wrong-id", exptime_s=1.0)


@pytest.mark.asyncio
async def test_abort_exposure_rejected_without_lease() -> None:
    """abort_exposure must raise LeaseNotFound when no lease is active."""
    svc, _ = _build_service(lease=None)

    with pytest.raises(LeaseNotFound):
        await svc.abort_exposure(lease_id="wrong-id")


@pytest.mark.asyncio
async def test_abort_exposure_rejected_when_idle() -> None:
    """abort_exposure must raise NoActiveExposure when no exposure is in progress."""
    svc, _ = _build_service(lease=_active_lease())

    with pytest.raises(NoActiveExposure):
        await svc.abort_exposure(lease_id="lease-1")


# ---------------------------------------------------------------------------
# Duplicate exposure resilience
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_exposure_rejected_when_already_exposing() -> None:
    """A second start_exposure while one is in progress must raise ExposureInProgress."""
    lease = _active_lease()
    svc, _ = _build_service(lease=lease)

    await svc.start_exposure(lease_id="lease-1", exptime_s=5.0)
    # Force the status to EXPOSING as the adapter callback would
    svc._current_capture.status = CaptureStatus.EXPOSING

    with pytest.raises(ExposureInProgress):
        await svc.start_exposure(lease_id="lease-1", exptime_s=5.0)


# ---------------------------------------------------------------------------
# is_exposing helper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_exposing_returns_false_when_idle() -> None:
    """is_exposing must return False when no capture is running."""
    svc, _ = _build_service(lease=_active_lease())

    assert svc.is_exposing() is False


@pytest.mark.asyncio
async def test_is_exposing_returns_true_during_exposure() -> None:
    """is_exposing must return True once an exposure has started."""
    lease = _active_lease()
    svc, _ = _build_service(lease=lease)

    await svc.start_exposure(lease_id="lease-1", exptime_s=2.0)
    svc._current_capture.status = CaptureStatus.EXPOSING

    assert svc.is_exposing() is True


@pytest.mark.asyncio
async def test_is_exposing_returns_false_after_completion() -> None:
    """is_exposing must return False once the capture has completed."""
    lease = _active_lease()
    svc, _ = _build_service(lease=lease)

    await svc.start_exposure(lease_id="lease-1", exptime_s=2.0)
    svc._current_capture.status = CaptureStatus.EXPOSING
    assert svc.is_exposing() is True

    svc._current_capture.mark_as_completed(file_path="/data/img.fits", sidecar_ok=True)
    assert svc.is_exposing() is False


# ---------------------------------------------------------------------------
# Hardware failure resilience
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_exposure_raises_hardware_error_on_camera_failure() -> None:
    """If the camera raises during start_exposure, HardwareError must propagate."""
    lease = _active_lease()
    svc, camera = _build_service(lease=lease)
    camera.start_exposure.side_effect = RuntimeError("CCD not responding")

    with pytest.raises(HardwareError):
        await svc.start_exposure(lease_id="lease-1", exptime_s=1.0)


@pytest.mark.asyncio
async def test_capture_marked_failed_on_hardware_error() -> None:
    """When the camera fails, the capture entity must be in FAILED state."""
    lease = _active_lease()
    svc, camera = _build_service(lease=lease)
    camera.start_exposure.side_effect = RuntimeError("timeout")

    try:
        await svc.start_exposure(lease_id="lease-1", exptime_s=1.0)
    except HardwareError:
        pass

    assert svc._current_capture is not None
    assert svc._current_capture.status is CaptureStatus.FAILED


# ---------------------------------------------------------------------------
# Sequential exposure resilience
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sequential_exposures_do_not_conflict() -> None:
    """After a completed exposure, starting a new one must succeed."""
    lease = _active_lease()
    svc, _ = _build_service(lease=lease)

    await svc.start_exposure(lease_id="lease-1", exptime_s=1.0)
    svc._current_capture.mark_as_completed(file_path="/data/img.fits", sidecar_ok=True)

    # Second exposure must not raise ExposureInProgress
    second = await svc.start_exposure(lease_id="lease-1", exptime_s=1.0)
    assert second is not None
    assert second.status in (CaptureStatus.PENDING, CaptureStatus.EXPOSING)


@pytest.mark.asyncio
async def test_handle_exposure_failed_resets_state() -> None:
    """handle_exposure_failed must mark capture as failed and clean internal state."""
    lease = _active_lease()
    svc, _ = _build_service(lease=lease)

    await svc.start_exposure(lease_id="lease-1", exptime_s=2.0)
    svc._current_capture.status = CaptureStatus.EXPOSING
    capture_id = svc._current_capture.id

    await svc.handle_exposure_failed("simulated hardware fault")

    assert svc._current_capture is not None
    assert svc._current_capture.id == capture_id
    assert svc._current_capture.status is CaptureStatus.FAILED
    assert svc._exposure_ok_received is False
