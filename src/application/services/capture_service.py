"""Capture orchestration application service."""

from __future__ import annotations

import asyncio
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

# How long to wait for a FITS file to appear on disk after the image-received
# event fires. Some drivers write the header before closing the file.
_FILE_WAIT_RETRIES = 5
_FILE_WAIT_INTERVAL_S = 0.4

# Path prefix pairs tried when resolving the image path inside the container.
# Add more pairs here if the bind-mount layout changes.
_PATH_TRANSLATIONS: list[tuple[str, str]] = [
    ("/opt/cavex/data", "/data"),
    ("/opt/cavex/data", "/opt/cavex/data"),  # same-path bind mount
    ("/data", "/opt/cavex/data"),             # INDIGO reports container path, resolve to host
]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _resolve_path(raw_path: str) -> str | None:
    """Try to locate raw_path on disk, applying known bind-mount translations.

    Returns the first resolvable path, or None if the file cannot be found.
    """
    # 1. Try as-is first (handles same-path bind mounts and absolute URLs)
    if os.path.isfile(raw_path):
        return raw_path

    # 2. Apply known host→container path translations
    for host_prefix, container_prefix in _PATH_TRANSLATIONS:
        if raw_path.startswith(host_prefix):
            candidate = container_prefix + raw_path[len(host_prefix):]
            if os.path.isfile(candidate):
                return candidate

    return None


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
        frame_type: str = "LIGHT",
        job_id: str | None = None,
    ) -> Capture:
        """Start a single exposure (async, returns immediately)."""
        self._lease_manager.require_lease(lease_id)

        if self._current_capture and self._current_capture.status == CaptureStatus.EXPOSING:
            raise ExposureInProgress("Cannot start: another exposure is in progress.")

        state = self._camera.get_state()

        capture = Capture(
            id=f"exp_{uuid4().hex[:8]}",
            lease_id=lease_id,
            exptime_s=exptime_s,
            frame_type=FrameType(frame_type),
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

    @staticmethod
    def _make_unique_path(file_path: str, capture_id: str) -> str:
        """Return a unique path so repeated exposures never overwrite each other.

        INDIGO drivers (webcam, simulator) reuse the same filename on every
        exposure.  Renaming the file right after detection gives each capture
        its own permanent location before the sidecar is written.
        """
        directory = os.path.dirname(file_path) or "."
        name, ext = os.path.splitext(os.path.basename(file_path))
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        short_id = capture_id.replace("exp_", "")[:8]
        return os.path.join(directory, f"{name}_{ts}_{short_id}{ext}")

    async def handle_exposure_complete(self, file_path: str) -> None:
        """Called when INDIGO signals exposure completion."""
        capture = self._current_capture
        if capture is None or capture.status != CaptureStatus.EXPOSING:
            logger.warning("exposure_complete_no_capture", file_path=file_path)
            return

        # Rename to a unique path so the next exposure can reuse the base filename.
        unique_path = self._make_unique_path(file_path, capture.id)
        if os.path.isfile(file_path):
            try:
                os.rename(file_path, unique_path)
                file_path = unique_path
                logger.info("image_renamed_unique", path=file_path)
            except OSError as exc:
                logger.warning("image_rename_failed", original=file_path, error=str(exc))
                # Continue with original path — better a successful sidecar than nothing

        state = self._camera.get_state()
        lease = self._lease_manager.get_current_lease()
        filename = file_path.rsplit("/", 1)[-1] if "/" in file_path else file_path

        metadata = ImageMetadata(
            filename=filename,
            timestamp_utc=_now_utc(),
            device_name=state.device_name,
            device_model=state.device_name,
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
        """Called when INDIGO signals exposure failure."""
        capture = self._current_capture
        if capture is None or capture.status != CaptureStatus.EXPOSING:
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
        """Abort the current in-progress exposure."""
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

    def is_exposing(self) -> bool:
        """Return True if an exposure is currently in progress."""
        return (
            self._current_capture is not None
            and self._current_capture.status == CaptureStatus.EXPOSING
        )

    def get_current_capture(self) -> Capture | None:
        """Return the current capture reference."""
        return self._current_capture

    # ── Private event handlers ───────────────────────────────────────

    async def _on_exposure_state_changed(self, previous: str, current: str) -> None:
        """Handle camera exposure state transitions.

        INDIGO drivers use different conventions to signal completion:
          - QHY / most CCDs:  BUSY -> Ok
          - Webcam / simulator: BUSY -> Idle  (treated as completion here)
          - Any driver on error: -> Alert
        """
        previous_state = previous.upper()
        current_state = current.upper()

        logger.info(
            "capture_service_state_change",
            previous=previous_state,
            current=current_state,
        )

        if current_state == "ALERT":
            await self.handle_exposure_failed("CCD_EXPOSURE entered ALERT state.")
            return

        # Both "Ok" and "Idle" after "Busy" mean the exposure finished.
        if previous_state == "BUSY" and current_state in ("OK", "IDLE"):
            self._exposure_ok_received = True
            logger.info(
                "exposure_ok_received",
                capture_id=self._current_capture.id if self._current_capture else "none",
            )
            await self._try_complete_capture()

            # Directory-scan fallback: some drivers (v4l2 webcam, simulator)
            # write the FITS file but never send CCD_IMAGE_FILE / CCD_IMAGE BLOB.
            # After a short wait for the normal path, scan the local-mode
            # directory for the newest file written since the exposure started.
            if self._pending_image_path is None:
                logger.info(
                    "image_path_not_received_scanning_directory",
                    capture_id=self._current_capture.id if self._current_capture else "none",
                )
                await asyncio.sleep(1.5)  # give INDIGO a moment to flush the file
                if self._pending_image_path is None:
                    await self._scan_for_new_image()

    async def _on_image_received(self, raw_path: str) -> None:
        """Handle image path emitted by the camera adapter.

        Applies path translation (host ↔ container bind-mount) and waits
        briefly for the file to be fully written before proceeding.
        """
        logger.info("image_path_received", raw_path=raw_path)

        resolved = await self._wait_for_file(raw_path)
        if resolved is None:
            logger.warning(
                "capture_image_missing",
                raw_path=raw_path,
                tried=_PATH_TRANSLATIONS,
            )
            await self.handle_exposure_failed(
                f"Image file not found after retries: {raw_path}"
            )
            return

        logger.info("image_file_resolved", resolved_path=resolved)
        self._pending_image_path = resolved
        await self._try_complete_capture()

    async def _try_complete_capture(self) -> None:
        """Complete capture when state and image conditions are both satisfied."""
        capture = self._current_capture
        if capture is None or capture.status != CaptureStatus.EXPOSING:
            return

        if self._exposure_ok_received and self._pending_image_path:
            await self.handle_exposure_complete(self._pending_image_path)

    # ── File resolution helper ───────────────────────────────────────


    async def _scan_for_new_image(self) -> None:
        """Scan the camera local-mode directory for a file written after the
        exposure started.  Used as a fallback when the INDIGO driver writes
        the image but never emits CCD_IMAGE_FILE or CCD_IMAGE BLOB events
        (common with v4l2 webcam and INDIGO simulator drivers).
        """
        capture = self._current_capture
        state   = self._camera.get_state()

        # Determine the directory to scan
        search_dirs: list[str] = []
        if state.local_mode_dir:
            search_dirs.append(state.local_mode_dir)
        # Also try the translated container path
        for host_prefix, container_prefix in _PATH_TRANSLATIONS:
            if state.local_mode_dir and state.local_mode_dir.startswith(host_prefix):
                candidate_dir = container_prefix + state.local_mode_dir[len(host_prefix):]
                if candidate_dir not in search_dirs:
                    search_dirs.append(candidate_dir)
        # Fallback: common known paths
        for fallback in ("/opt/cavex/data", "/data"):
            if fallback not in search_dirs:
                search_dirs.append(fallback)

        # Timestamp to compare against (exposure start or service start)
        since: datetime | None = None
        if capture is not None and capture.started_at is not None:
            since = capture.started_at

        extensions = {".fits", ".fit", ".jpg", ".jpeg", ".png", ".tiff", ".tif"}

        for directory in search_dirs:
            if not os.path.isdir(directory):
                logger.debug("scan_dir_not_found", directory=directory)
                continue

            newest_path: str | None = None
            newest_mtime: float = 0.0

            for fname in os.listdir(directory):
                if not any(fname.lower().endswith(ext) for ext in extensions):
                    continue
                fpath = os.path.join(directory, fname)
                try:
                    mtime = os.path.getmtime(fpath)
                except OSError:
                    continue
                if since is not None:
                    file_time = datetime.fromtimestamp(mtime, tz=timezone.utc)
                    if file_time < since:
                        continue
                if mtime > newest_mtime:
                    newest_mtime = mtime
                    newest_path = fpath

            if newest_path is not None:
                logger.info(
                    "directory_scan_found_image",
                    path=newest_path,
                    directory=directory,
                )
                self._pending_image_path = newest_path
                await self._try_complete_capture()
                return

        logger.warning(
            "directory_scan_no_image_found",
            searched=search_dirs,
            since=since.isoformat() if since else None,
        )
        await self.handle_exposure_failed(
            "Exposure completed but no image file found in local-mode directory."
        )

    @staticmethod
    async def _wait_for_file(raw_path: str) -> str | None:
        """Wait up to _FILE_WAIT_RETRIES × _FILE_WAIT_INTERVAL_S for the file
        to appear, trying all known path translations on each attempt.

        Returns the resolved path, or None if it never appears.
        """
        for attempt in range(_FILE_WAIT_RETRIES):
            resolved = _resolve_path(raw_path)
            if resolved is not None:
                return resolved
            logger.debug(
                "waiting_for_image_file",
                raw_path=raw_path,
                attempt=attempt + 1,
            )
            await asyncio.sleep(_FILE_WAIT_INTERVAL_S)

        return None