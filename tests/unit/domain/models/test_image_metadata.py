"""Unit tests for ImageMetadata model."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from src.domain.models.image_metadata import ImageMetadata


def _sample_metadata() -> ImageMetadata:
    """Create a sample metadata instance."""
    return ImageMetadata(
        filename="cavex_0042.fits",
        timestamp_utc=datetime.now(timezone.utc),
        device_name="CAVEXCam",
        device_model="QHY600",
        exptime_s=3.0,
        frame_type="LIGHT",
        gain=26,
        offset=10,
        binning=(1, 1),
        roi=(0, 0, 9600, 6422),
        ccd_temp_c=-10.2,
        cooler_power_pct=45.5,
        lease_owner="cavex_manager",
        job_id="night_001",
        sequence_id=None,
        exposure_id="exp_a1b2c3d4",
        service_version="1.0.0",
    )


def test_metadata_creation_with_valid_values() -> None:
    """Test metadata creation with all spec-required fields."""
    metadata = _sample_metadata()

    assert metadata.filename == "cavex_0042.fits"
    assert metadata.device_name == "CAVEXCam"
    assert metadata.exptime_s == 3.0
    assert metadata.binning == (1, 1)
    assert metadata.roi == (0, 0, 9600, 6422)
    assert metadata.lease_owner == "cavex_manager"
    assert metadata.exposure_id == "exp_a1b2c3d4"


def test_metadata_is_immutable() -> None:
    """Test frozen dataclass immutability."""
    metadata = _sample_metadata()

    with pytest.raises(FrozenInstanceError):
        metadata.gain = 200  # type: ignore[misc]


def test_metadata_with_none_optionals() -> None:
    """Test metadata with None optional fields."""
    metadata = ImageMetadata(
        filename="test.fits",
        timestamp_utc=datetime.now(timezone.utc),
        device_name="Test",
        device_model="Simulator",
        exptime_s=1.0,
        frame_type="DARK",
        gain=None,
        offset=None,
        binning=(2, 2),
        roi=(0, 0, 4800, 3211),
        ccd_temp_c=-5.0,
        cooler_power_pct=80.0,
        lease_owner="test",
        job_id=None,
        sequence_id=None,
        exposure_id="exp_test",
        service_version="1.0.0",
    )

    assert metadata.gain is None
    assert metadata.offset is None
    assert metadata.job_id is None
