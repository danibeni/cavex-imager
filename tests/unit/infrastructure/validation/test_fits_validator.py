"""Unit tests for FitsValidator."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from src.infrastructure.validation.fits_validator import FitsValidator


@pytest.fixture()
def validator() -> FitsValidator:
    return FitsValidator()


def _write_valid_fits(path: Path) -> None:
    """Write a minimal valid FITS file."""
    data = np.zeros((10, 10), dtype=np.uint16)
    hdu = fits.PrimaryHDU(data)
    hdu.writeto(str(path), overwrite=True)


def test_valid_fits_returns_true(tmp_path: Path, validator: FitsValidator) -> None:
    """Test that a properly written FITS file is accepted."""
    fits_path = tmp_path / "valid.fits"
    _write_valid_fits(fits_path)

    valid, error = validator.validate(str(fits_path))

    assert valid is True
    assert error is None


def test_missing_file_returns_false(validator: FitsValidator) -> None:
    """Test that a missing file is rejected with an error message."""
    valid, error = validator.validate("/tmp/does_not_exist_cavex.fits")

    assert valid is False
    assert error is not None
    assert "not found" in error.lower()


def test_empty_file_returns_false(tmp_path: Path, validator: FitsValidator) -> None:
    """Test that an empty file is rejected."""
    empty = tmp_path / "empty.fits"
    empty.write_bytes(b"")

    valid, error = validator.validate(str(empty))

    assert valid is False
    assert error is not None


def test_corrupt_file_returns_false(tmp_path: Path, validator: FitsValidator) -> None:
    """Test that a file with corrupt content is rejected."""
    corrupt = tmp_path / "corrupt.fits"
    corrupt.write_bytes(b"this is not a fits file at all")

    valid, error = validator.validate(str(corrupt))

    assert valid is False
    assert error is not None


def test_fits_with_no_data_hdu_returns_true(tmp_path: Path, validator: FitsValidator) -> None:
    """Test that a FITS file with an empty primary HDU is still valid."""
    fits_path = tmp_path / "nodatablock.fits"
    hdu = fits.PrimaryHDU()
    hdu.writeto(str(fits_path), overwrite=True)

    valid, error = validator.validate(str(fits_path))

    assert valid is True
    assert error is None
