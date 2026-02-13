"""Sidecar JSON writer with atomic write (tmp + rename)."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

import structlog

from src.domain.models.image_metadata import ImageMetadata
from src.domain.ports.storage import IStorage, StorageValidation

logger = structlog.get_logger(__name__)

_MIN_FREE_GB_DEFAULT = 10.0


class SidecarWriter(IStorage):
    """Writes capture metadata sidecar files with atomic rename."""

    def __init__(self, min_free_gb: float = _MIN_FREE_GB_DEFAULT) -> None:
        """Initialize writer.

        Args:
            min_free_gb: Minimum free GB threshold for disk space checks.
        """
        self._min_free_gb = min_free_gb

    async def validate_path(self, path: str) -> StorageValidation:
        """Validate storage path exists, is writable, and has space."""
        p = Path(path)

        # Create directory if needed
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return StorageValidation(valid=False, writable=False, disk_free_gb=0.0, error=str(exc))

        # Check writable by creating a temp file
        writable = False
        try:
            test_file = p / ".cavex_write_test"
            test_file.write_text("ok")
            test_file.unlink()
            writable = True
        except OSError:
            pass

        disk_free_gb = await self.check_disk_space(path)
        space_ok = disk_free_gb >= self._min_free_gb

        error = None
        if not writable:
            error = "Directory is not writable"
        elif not space_ok:
            error = f"Insufficient disk space: {disk_free_gb:.1f}GB (min {self._min_free_gb}GB)"

        return StorageValidation(
            valid=writable and space_ok,
            writable=writable,
            disk_free_gb=disk_free_gb,
            error=error,
        )

    async def check_disk_space(self, path: str) -> float:
        """Return free disk space in GB."""
        try:
            usage = shutil.disk_usage(path)
            return usage.free / (1024**3)
        except OSError:
            return 0.0

    async def write_sidecar(self, fits_path: str, metadata: ImageMetadata) -> bool:
        """Write JSON sidecar file next to FITS image using atomic rename.

        Args:
            fits_path: Target FITS file path.
            metadata: Structured image metadata.

        Returns:
            True on successful write, False otherwise.
        """
        sidecar_path = fits_path.replace(".fits", ".json")
        payload = self._build_sidecar_payload(metadata)

        try:
            # Write to temporary file, then atomic rename
            dir_name = os.path.dirname(sidecar_path) or "."
            fd, tmp_path = tempfile.mkstemp(suffix=".json.tmp", dir=dir_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2, default=str)
                os.rename(tmp_path, sidecar_path)
            except Exception:
                # Clean up tmp on failure
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise

            logger.info("sidecar_written", path=sidecar_path)
            return True
        except Exception as error:
            logger.warning("sidecar_write_failed", path=sidecar_path, error=str(error))
            return False

    @staticmethod
    def _build_sidecar_payload(m: ImageMetadata) -> dict:
        """Build the sidecar JSON structure matching the spec."""
        return {
            "filename": m.filename,
            "timestamp_utc": m.timestamp_utc.isoformat(),
            "device": {
                "name": m.device_name,
                "model": m.device_model,
            },
            "acquisition": {
                "exptime_s": m.exptime_s,
                "frame_type": m.frame_type,
                "gain": m.gain,
                "offset": m.offset,
                "binning": list(m.binning),
                "roi": list(m.roi),
            },
            "thermal": {
                "ccd_temp_c": m.ccd_temp_c,
                "cooler_power_pct": m.cooler_power_pct,
            },
            "context": {
                "lease_owner": m.lease_owner,
                "job_id": m.job_id,
                "sequence_id": m.sequence_id,
                "exposure_id": m.exposure_id,
            },
            "cavex": {
                "service_version": m.service_version,
            },
        }
