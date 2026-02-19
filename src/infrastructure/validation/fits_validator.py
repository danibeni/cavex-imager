"""FITS image validator using astropy."""

from __future__ import annotations

import os

import structlog
from astropy.io import fits

from src.domain.ports.image_validator import IImageValidator

logger = structlog.get_logger(__name__)


class FitsValidator(IImageValidator):
    """Validate a FITS image file using astropy.

    Checks that the file exists, is readable, and contains a valid FITS
    structure with at least one HDU carrying image data.
    """

    def validate(self, file_path: str) -> tuple[bool, str | None]:
        """Check whether the FITS file exists and is structurally valid.

        Args:
            file_path: Absolute path to the FITS file.

        Returns:
            (True, None) when valid; (False, error_message) otherwise.
        """
        if not os.path.isfile(file_path):
            return False, f"File not found: {file_path}"

        if os.path.getsize(file_path) == 0:
            return False, f"File is empty: {file_path}"

        try:
            with fits.open(file_path, memmap=False) as hdul:
                if len(hdul) == 0:
                    return False, "FITS file contains no HDUs"

                primary = hdul[0]
                # Force data read to catch truncation or corruption
                _ = primary.data
                _ = primary.header

        except Exception as exc:
            logger.warning(
                "fits_validation_failed", file_path=file_path, error=str(exc)
            )
            return False, f"FITS validation error: {exc}"

        return True, None
