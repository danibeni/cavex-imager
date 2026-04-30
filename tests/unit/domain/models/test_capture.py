"""Unit tests for Capture model."""

from __future__ import annotations

from datetime import datetime

import pytest

from src.domain.models.capture import Capture, CaptureStatus, FrameType


def _new_capture() -> Capture:
    """Create a valid capture instance for tests."""
    return Capture(
        id="capture-1",
        lease_id="lease-1",
        exptime_s=10.0,
        frame_type=FrameType.LIGHT,
        binning=(1, 1),
        status=CaptureStatus.PENDING,
        job_id="night_001",
    )


def test_capture_creation_with_valid_values() -> None:
    """Test capture creation with valid values."""
    capture = _new_capture()

    assert capture.id == "capture-1"
    assert capture.status is CaptureStatus.PENDING
    assert capture.file_path is None
    assert capture.sidecar_written is False
    assert capture.job_id == "night_001"
    assert capture.sequence_id is None


def test_valid_state_transitions() -> None:
    """Test expected state transitions from pending to completed."""
    capture = _new_capture()

    capture.mark_as_exposing()
    assert capture.status is CaptureStatus.EXPOSING

    capture.mark_as_completed("/data/images/cavex_0001.fits", sidecar_ok=True)
    assert capture.status is CaptureStatus.COMPLETED
    assert capture.sidecar_written is True


def test_mark_as_exposing_updates_started_at() -> None:
    """Test mark_as_exposing updates started_at timestamp."""
    capture = _new_capture()

    capture.mark_as_exposing()

    assert isinstance(capture.started_at, datetime)


def test_mark_as_completed_updates_completed_at_and_file_path() -> None:
    """Test mark_as_completed updates completion metadata."""
    capture = _new_capture()
    file_path = "/data/images/cavex_0002.fits"

    capture.mark_as_completed(file_path=file_path, sidecar_ok=False)

    assert isinstance(capture.completed_at, datetime)
    assert capture.file_path == file_path
    assert capture.sidecar_written is False


def test_mark_as_failed_updates_error_message() -> None:
    """Test mark_as_failed stores the error message."""
    capture = _new_capture()

    capture.mark_as_failed("camera disconnected")

    assert capture.status is CaptureStatus.FAILED
    assert capture.error_message == "camera disconnected"


def test_mark_as_aborted_updates_status() -> None:
    """Test mark_as_aborted sets correct state."""
    capture = _new_capture()
    capture.mark_as_exposing()

    capture.mark_as_aborted()

    assert capture.status is CaptureStatus.ABORTED
    assert capture.completed_at is not None


def test_invalid_status_raises_value_error() -> None:
    """Test that passing a raw string as status raises ValueError."""
    with pytest.raises(ValueError, match="status"):
        Capture(
            id="c1",
            lease_id="l1",
            exptime_s=5.0,
            frame_type=FrameType.LIGHT,
            binning=(1, 1),
            status="pending",  # type: ignore[arg-type]
        )


def test_invalid_frame_type_raises_value_error() -> None:
    """Test that passing a raw string as frame_type raises ValueError."""
    with pytest.raises(ValueError, match="frame_type"):
        Capture(
            id="c1",
            lease_id="l1",
            exptime_s=5.0,
            frame_type="LIGHT",  # type: ignore[arg-type]
            binning=(1, 1),
            status=CaptureStatus.PENDING,
        )


def test_zero_exptime_raises_value_error() -> None:
    """Test that exptime_s=0 raises ValueError."""
    with pytest.raises(ValueError, match="exptime_s"):
        Capture(
            id="c1",
            lease_id="l1",
            exptime_s=0.0,
            frame_type=FrameType.LIGHT,
            binning=(1, 1),
            status=CaptureStatus.PENDING,
        )


def test_invalid_binning_raises_value_error() -> None:
    """Test that binning with a zero value raises ValueError."""
    with pytest.raises(ValueError, match="binning"):
        Capture(
            id="c1",
            lease_id="l1",
            exptime_s=1.0,
            frame_type=FrameType.LIGHT,
            binning=(0, 1),
            status=CaptureStatus.PENDING,
        )
