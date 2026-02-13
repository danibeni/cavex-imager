"""Data transfer objects for capture operations."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ExposureStartRequest(BaseModel):
    """Payload for starting a single exposure."""

    exptime_s: float = Field(gt=0.001, le=3600, description="Exposure time in seconds")
    job_id: str | None = Field(default=None, max_length=100)


class ExposureStartResponse(BaseModel):
    """Response after exposure start accepted."""

    exposure_id: str
    exptime_s: float
    started_at: datetime
    estimated_done_at: datetime


class SequenceStartRequest(BaseModel):
    """Payload for starting an exposure sequence."""

    exptime_s: float = Field(gt=0.001, le=3600)
    count: int = Field(ge=0, description="Number of exposures (0 = infinite)")
    period_s: float = Field(gt=0)
    drift_mode: str = Field(default="from_start", pattern="^(from_start|from_end)$")
    job_id: str | None = Field(default=None, max_length=100)


class SequenceStartResponse(BaseModel):
    """Response after sequence start accepted."""

    sequence_id: str
    exptime_s: float
    count: int
    period_s: float
    started_at: datetime


class SequenceStopRequest(BaseModel):
    """Payload for stopping a sequence."""

    sequence_id: str


class CaptureStatusResponse(BaseModel):
    """Current capture status."""

    capture_id: str | None = None
    status: str
    file_path: str | None = None
    sidecar_written: bool = False
    error_message: str | None = None
