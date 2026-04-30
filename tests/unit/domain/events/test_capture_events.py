"""Unit tests for capture domain events."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.domain.events.capture_events import (
    CaptureAborted,
    CaptureCompleted,
    CaptureFailed,
    CaptureStarted,
    ImageReady,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def test_capture_started_stores_fields() -> None:
    """Test CaptureStarted dataclass stores all fields correctly."""
    ts = _now()
    event = CaptureStarted(
        capture_id="exp_001",
        lease_id="lease-1",
        exptime_s=5.0,
        job_id="night_001",
        timestamp=ts,
    )

    assert event.capture_id == "exp_001"
    assert event.lease_id == "lease-1"
    assert event.exptime_s == 5.0
    assert event.job_id == "night_001"
    assert event.timestamp == ts


def test_capture_started_without_job_id() -> None:
    """Test CaptureStarted with job_id=None."""
    event = CaptureStarted(
        capture_id="exp_002",
        lease_id="lease-1",
        exptime_s=10.0,
        job_id=None,
        timestamp=_now(),
    )

    assert event.job_id is None


def test_capture_started_is_immutable() -> None:
    """Test CaptureStarted is frozen (immutable dataclass)."""
    event = CaptureStarted(
        capture_id="exp_001",
        lease_id="lease-1",
        exptime_s=1.0,
        job_id=None,
        timestamp=_now(),
    )

    with pytest.raises((AttributeError, TypeError)):
        event.capture_id = "other"  # type: ignore[misc]


def test_capture_completed_stores_fields() -> None:
    """Test CaptureCompleted dataclass stores all fields."""
    ts = _now()
    event = CaptureCompleted(
        capture_id="exp_003",
        job_id="batch_42",
        exptime_s=30.0,
        file_path="/data/cavex_0042.fits",
        sidecar_written=True,
        ccd_temp_c=-15.0,
        timestamp=ts,
    )

    assert event.capture_id == "exp_003"
    assert event.file_path == "/data/cavex_0042.fits"
    assert event.sidecar_written is True
    assert event.ccd_temp_c == -15.0


def test_image_ready_stores_fields() -> None:
    """Test ImageReady dataclass stores image and sidecar paths."""
    ts = _now()
    event = ImageReady(
        capture_id="exp_004",
        image_path="/data/cavex_0042.fits",
        sidecar_path="/data/cavex_0042.json",
        timestamp=ts,
    )

    assert event.image_path == "/data/cavex_0042.fits"
    assert event.sidecar_path == "/data/cavex_0042.json"


def test_capture_failed_stores_error_message() -> None:
    """Test CaptureFailed dataclass stores error message."""
    event = CaptureFailed(
        capture_id="exp_005",
        error_message="CCD_EXPOSURE entered ALERT state.",
        timestamp=_now(),
    )

    assert event.capture_id == "exp_005"
    assert "ALERT" in event.error_message


def test_capture_aborted_stores_capture_id() -> None:
    """Test CaptureAborted dataclass stores capture id."""
    event = CaptureAborted(
        capture_id="exp_006",
        timestamp=_now(),
    )

    assert event.capture_id == "exp_006"
    assert isinstance(event.timestamp, datetime)


def test_events_equality_based_on_fields() -> None:
    """Test that two events with the same fields are equal (frozen dataclass)."""
    ts = _now()
    a = CaptureFailed(capture_id="x", error_message="err", timestamp=ts)
    b = CaptureFailed(capture_id="x", error_message="err", timestamp=ts)

    assert a == b
