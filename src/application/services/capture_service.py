"""Capture orchestration application service."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

import structlog

from src.application.services.lease_manager import LeaseManager
from src.domain.exceptions.capture_exceptions import (
    ExposureInProgress,
    HardwareError,
    NoActiveExposure,
)
from src.domain.models.capture import Capture, CaptureStatus, FrameType
from src.domain.models.image_metadata import ImageMetadata
from src.domain.ports.camera_device import ICameraDevice
from src.domain.ports.event_publisher import IEventPublisher
from src.domain.ports.image_validator import IImageValidator
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
        image_validator: IImageValidator,
        service_version: str = "1.0.0",
    ) -> None:
        """Initialize capture service dependencies.

        Args:
            camera: Camera device port.
            storage: Sidecar storage port.
            event_publisher: Event publisher port.
            lease_manager: Lease manager service.
            image_validator: FITS image validator port.
            service_version: Service version for sidecar metadata.
        """
        self._camera = camera
        self._storage = storage
        self._event_publisher = event_publisher
        self._lease_manager = lease_manager
        self._image_validator = image_validator
        self._service_version = service_version
        self._current_capture: Capture | None = None
        self._exposure_ok_received = False
        self._pending_image_path: str | None = None

        self._camera.subscribe_to_exposure_events(
            on_state_changed=self._on_exposure_state_changed,
            on_image_received=self._on_image_received,
        )

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
        self._lease_manager.require_lease(lease_id)

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
        self._exposure_ok_received = False
        self._pending_image_path = None

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

        image_valid, image_error = self._image_validator.validate(file_path)
        sidecar_ok = await self._storage.write_sidecar(fits_path=file_path, metadata=metadata)
        capture.mark_as_completed(file_path=file_path, sidecar_ok=sidecar_ok)

        await self._event_publisher.publish(
            "capture.completed",
            {
                "capture_id": capture.id,
                "job_id": capture.job_id,
                "exptime_s": capture.exptime_s,
                "file_path": file_path,
                "sidecar_path": file_path.replace(".fits", ".json"),
                "image_valid": image_valid,
                "image_error": image_error,
                "sidecar_written": sidecar_ok,
                "ccd_temp_c": state.ccd_temp_c,
            },
        )

        if image_valid and sidecar_ok:
            logger.info("exposure_completed", capture_id=capture.id, file_path=file_path)
        elif not image_valid:
            logger.warning(
                "exposure_completed_image_invalid",
                capture_id=capture.id,
                error=image_error,
            )
        else:
            logger.warning("exposure_completed_sidecar_failed", capture_id=capture.id)

        self._exposure_ok_received = False
        self._pending_image_path = None

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
        self._exposure_ok_received = False
        self._pending_image_path = None

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
        self._exposure_ok_received = False
        self._pending_image_path = None

    def get_current_capture(self) -> Capture | None:
        """Return the current capture reference."""
        return self._current_capture

    async def _on_exposure_state_changed(self, previous: str, current: str) -> None:
        """Handle camera exposure state transitions."""
        previous_state = previous.upper()
        current_state = current.upper()

        if current_state == "ALERT":
            await self.handle_exposure_failed("CCD_EXPOSURE entered ALERT state.")
            return

        if previous_state == "BUSY" and current_state == "OK":
            self._exposure_ok_received = True
            await self._try_complete_capture()

    async def _on_image_received(self, file_path: str) -> None:
        """Handle image path emitted by the camera adapter."""
        if not os.path.isfile(file_path):
            logger.warning("capture_image_missing", file_path=file_path)
            await self.handle_exposure_failed(f"Image file not found: {file_path}")
            return

        self._pending_image_path = file_path
        await self._try_complete_capture()

    async def _try_complete_capture(self) -> None:
        """Complete capture when state and image conditions are satisfied."""
        capture = self._current_capture
        if capture is None or capture.status != CaptureStatus.EXPOSING:
            return

        if self._exposure_ok_received and self._pending_image_path:
            await self.handle_exposure_complete(self._pending_image_path)
