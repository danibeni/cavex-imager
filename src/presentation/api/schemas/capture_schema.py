"""Capture API schemas.

Examples:
    `ExposureStartRequest(exptime_s=3.0, job_id="night_001")`
    `SequenceStartRequest(exptime_s=3.0, count=10, period_s=5.0, drift_mode="from_start")`
    `SequenceStopRequest(sequence_id="seq_a1b2c3d4")`
"""

from src.application.dtos.capture_dto import (
    CaptureStatusResponse,
    ExposureStartRequest,
    ExposureStartResponse,
    SequenceStartRequest,
    SequenceStartResponse,
    SequenceStopRequest,
)

__all__ = [
    "ExposureStartRequest",
    "ExposureStartResponse",
    "SequenceStartRequest",
    "SequenceStartResponse",
    "SequenceStopRequest",
    "CaptureStatusResponse",
]
