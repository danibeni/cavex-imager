"""Unit tests for capture DTOs."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.application.dtos.capture_dto import ExposureStartRequest, SequenceStartRequest


def test_exposure_start_validates_with_valid_values() -> None:
    """Test ExposureStartRequest validation with valid values."""
    dto = ExposureStartRequest(exptime_s=3.0, job_id="night_001")
    assert dto.exptime_s == 3.0
    assert dto.job_id == "night_001"


def test_exposure_start_allows_none_job_id() -> None:
    """Test ExposureStartRequest allows None job_id."""
    dto = ExposureStartRequest(exptime_s=10.0)
    assert dto.job_id is None


def test_exposure_start_fails_when_exptime_too_small() -> None:
    """Test ExposureStartRequest fails when exptime_s is too small."""
    with pytest.raises(ValidationError):
        ExposureStartRequest(exptime_s=0.0001)


def test_exposure_start_fails_when_exptime_too_large() -> None:
    """Test ExposureStartRequest fails when exptime_s exceeds max."""
    with pytest.raises(ValidationError):
        ExposureStartRequest(exptime_s=3601)


def test_sequence_start_validates_with_valid_values() -> None:
    """Test SequenceStartRequest validation."""
    dto = SequenceStartRequest(exptime_s=3.0, count=10, period_s=5.0)
    assert dto.count == 10
    assert dto.drift_mode == "from_start"


def test_sequence_start_fails_with_invalid_drift_mode() -> None:
    """Test SequenceStartRequest fails with invalid drift_mode."""
    with pytest.raises(ValidationError):
        SequenceStartRequest(exptime_s=3.0, count=10, period_s=5.0, drift_mode="invalid")
