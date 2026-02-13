"""Capture orchestration application service."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import structlog

from src.application.services.lease_manager import LeaseManager
from src.domain.exceptions.capture_exceptions import (
    ExposureInProgress,
    HardwareError,
    NoActiveExposure,
)
from src.domain.exceptions.lease_exceptions import LeaseNotFound
from src.domain.models.capture import Capture, CaptureStatus, FrameType
from src.domain.models.image_metadata import ImageMetadata
from src.domain.ports.camera_device import ICameraDevice
from src.domain.ports.event_publisher import IEventPublisher
from src.domain.ports.storage import IStorage

logger = structlog.get_logger(__name__)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class CaptureService:
    """Service that coordinates capture lifecycle operations."""

    def __init__(
        self,
        camera: ICameraDevice,
        storage: IStorage,
        event_publisher: IEventPublisher,
        lease_manager: LeaseManager,
        service_version: str = "1.0.0",
    ) -> None:
        """Initialize capture service dependencies.

        Args:
            camera: Camera device port.
            storage: Sidecar storage port.
            event_publisher: Event publisher port.
            lease_manager: Lease manager service.
            service_version: Service version for sidecar metadata.
        """
        self._camera = camera
        self._storage = storage
        self._event_publisher = event_publisher
        self._lease_manager = lease_manager
        self._service_version = service_version
        self._current_capture: Capture | None = None

    async def start_exposure(
        self,
        lease_id: str,
        exptime_s: float,
        job_id: str | None = None,
    ) -> Capture:
        """Start a single exposure (async, returns immediately).

        Args:
            lease_id: Active lease identifier.
            exptime_s: Exposure duration in seconds.
            job_id: Optional correlation identifier.

        Returns:
            Capture entity in exposing state.

        Raises:
            LeaseNotFound: If lease is invalid or expired.
            ExposureInProgress: If another exposure is running.
            HardwareError: If camera command fails.
        """
        lease = self._lease_manager.require_lease(lease_id)

        if self._current_capture and self._current_capture.status == CaptureStatus.EXPOSING:
            raise ExposureInProgress("Cannot start: another exposure is in progress.")

        state = self._camera.get_state()

        capture = Capture(
            id=f"exp_{uuid4().hex[:8]}",
            lease_id=lease_id,
            exptime_s=exptime_s,
            frame_type=FrameType.LIGHT,
            binning=(state.bin_x, state.bin_y),
            status=CaptureStatus.PENDING,
            job_id=job_id,
        )
        self._current_capture = capture

        try:
            await self._camera.start_exposure(exptime_s)
            capture.mark_as_exposing()

            await self._event_publisher.publish(
                "capture.started",
                {
                    "capture_id": capture.id,
                    "lease_id": capture.lease_id,
                    "exptime_s": capture.exptime_s,
                    "job_id": capture.job_id,
                },
            )
            logger.info(
                "exposure_started",
                capture_id=capture.id,
                lease_id=lease_id,
                exptime_s=exptime_s,
            )
            return capture

        except Exception as error:
            capture.mark_as_failed(str(error))
            logger.exception("exposure_start_failed", capture_id=capture.id, error=str(error))
            raise HardwareError(str(error)) from error

    async def handle_exposure_complete(self, file_path: str) -> None:
        """Called when INDIGO signals exposure completion.

        Writes sidecar and publishes completion events.

        Args:
            file_path: FITS file path written by INDIGO.
        """
        capture = self._current_capture
        if capture is None or capture.status != CaptureStatus.EXPOSING:
            logger.warning("exposure_complete_no_capture", file_path=file_path)
            return

        state = self._camera.get_state()
        lease = self._lease_manager.get_current_lease()
        filename = file_path.rsplit("/", 1)[-1] if "/" in file_path else file_path

        metadata = ImageMetadata(
            filename=filename,
            timestamp_utc=_now_utc(),
            device_name=state.device_name,
            device_model="QHY600",
            exptime_s=capture.exptime_s,
            frame_type=capture.frame_type.value,
            gain=state.gain,
            offset=state.offset,
            binning=(state.bin_x, state.bin_y),
            roi=state.roi,
            ccd_temp_c=state.ccd_temp_c,
            cooler_power_pct=state.cooler_power_pct,
            lease_owner=lease.owner if lease else "unknown",
            job_id=capture.job_id,
            sequence_id=capture.sequence_id,
            exposure_id=capture.id,
            service_version=self._service_version,
        )

        sidecar_ok = await self._storage.write_sidecar(fits_path=file_path, metadata=metadata)
        capture.mark_as_completed(file_path=file_path, sidecar_ok=sidecar_ok)

        await self._event_publisher.publish(
            "capture.completed",
            {
                "capture_id": capture.id,
                "job_id": capture.job_id,
                "exptime_s": capture.exptime_s,
                "file_path": file_path,
                "sidecar_written": sidecar_ok,
                "ccd_temp_c": state.ccd_temp_c,
            },
        )
        await self._event_publisher.publish(
            "image.ready",
            {
                "capture_id": capture.id,
                "image_path": file_path,
                "sidecar_path": file_path.replace(".fits", ".json"),
            },
        )

        if sidecar_ok:
            logger.info("exposure_completed", capture_id=capture.id, file_path=file_path)
        else:
            logger.warning("exposure_completed_sidecar_failed", capture_id=capture.id)

    async def handle_exposure_failed(self, error_message: str) -> None:
        """Called when INDIGO signals exposure failure.

        Args:
            error_message: Error description.
        """
        capture = self._current_capture
        if capture is None:
            return

        capture.mark_as_failed(error_message)
        await self._event_publisher.publish(
            "capture.failed",
            {"capture_id": capture.id, "error_message": error_message},
        )
        logger.error("exposure_failed", capture_id=capture.id, error=error_message)

    async def abort_exposure(self, lease_id: str) -> None:
        """Abort the current in-progress exposure.

        Args:
            lease_id: Active lease identifier (must match).

        Raises:
            LeaseNotFound: If lease is invalid.
            NoActiveExposure: If there is no active capture.
        """
        self._lease_manager.require_lease(lease_id)

        if self._current_capture is None or self._current_capture.status != CaptureStatus.EXPOSING:
            raise NoActiveExposure("No active exposure to abort.")

        await self._camera.abort_exposure()
        self._current_capture.mark_as_aborted()

        await self._event_publisher.publish(
            "capture.aborted",
            {"capture_id": self._current_capture.id},
        )
        logger.info("exposure_aborted", capture_id=self._current_capture.id)

    def get_current_capture(self) -> Capture | None:
        """Return the current capture reference."""
        return self._current_capture
