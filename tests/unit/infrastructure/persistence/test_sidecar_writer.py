"""Unit tests for sidecar writer."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.domain.models.image_metadata import ImageMetadata
from src.infrastructure.persistence.sidecar_writer import SidecarWriter


def _sample_metadata() -> ImageMetadata:
    """Create sample metadata for tests."""
    return ImageMetadata(
        filename="cavex_0001.fits",
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
        exposure_id="exp_test",
        service_version="1.0.0",
    )


@pytest.mark.asyncio
async def test_write_sidecar_creates_json_with_correct_structure(tmp_path: Path) -> None:
    """Test write_sidecar creates JSON with spec-compliant structure."""
    writer = SidecarWriter()
    image_path = tmp_path / "cavex_0001.fits"
    metadata = _sample_metadata()

    result = await writer.write_sidecar(str(image_path), metadata)

    sidecar_path = tmp_path / "cavex_0001.json"
    assert result is True
    assert sidecar_path.exists()

    content = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert content["filename"] == "cavex_0001.fits"
    assert content["device"]["name"] == "CAVEXCam"
    assert content["device"]["model"] == "QHY600"
    assert content["acquisition"]["exptime_s"] == 3.0
    assert content["acquisition"]["gain"] == 26
    assert content["acquisition"]["binning"] == [1, 1]
    assert content["thermal"]["ccd_temp_c"] == -10.2
    assert content["context"]["lease_owner"] == "cavex_manager"
    assert content["context"]["exposure_id"] == "exp_test"
    assert content["cavex"]["service_version"] == "1.0.0"


@pytest.mark.asyncio
async def test_write_sidecar_atomic_no_tmp_leftover(tmp_path: Path) -> None:
    """Test write_sidecar uses atomic write (no .tmp file left)."""
    writer = SidecarWriter()
    image_path = tmp_path / "cavex_0002.fits"
    metadata = _sample_metadata()

    await writer.write_sidecar(str(image_path), metadata)

    # No .tmp files should remain
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert len(tmp_files) == 0


@pytest.mark.asyncio
async def test_write_sidecar_returns_false_on_bad_path() -> None:
    """Test write_sidecar returns False for invalid path."""
    writer = SidecarWriter()
    metadata = _sample_metadata()

    result = await writer.write_sidecar("/nonexistent/path/test.fits", metadata)

    assert result is False


@pytest.mark.asyncio
async def test_validate_path_success(tmp_path: Path) -> None:
    """Test validate_path succeeds for a valid writable directory."""
    writer = SidecarWriter(min_free_gb=0.001)

    result = await writer.validate_path(str(tmp_path))

    assert result.valid is True
    assert result.writable is True
    assert result.disk_free_gb > 0


@pytest.mark.asyncio
async def test_validate_path_creates_missing_directory(tmp_path: Path) -> None:
    """Test validate_path creates directory if it doesn't exist."""
    writer = SidecarWriter(min_free_gb=0.001)
    new_dir = tmp_path / "new" / "session"

    result = await writer.validate_path(str(new_dir))

    assert result.valid is True
    assert new_dir.exists()


@pytest.mark.asyncio
async def test_check_disk_space_returns_positive(tmp_path: Path) -> None:
    """Test check_disk_space returns a positive value."""
    writer = SidecarWriter()

    free_gb = await writer.check_disk_space(str(tmp_path))

    assert free_gb > 0
