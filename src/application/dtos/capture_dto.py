"""Data transfer objects for capture operations."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ExposureStartRequest(BaseModel):
    """Payload for starting a single exposure."""

    exptime_s: float = Field(
        gt=0.001,
        le=3600,
        description="Exposure time in seconds (0.001–3600).",
        examples=[5.0],
    )
    frame_type: str = Field(
        default="LIGHT",
        pattern="^(LIGHT|DARK|BIAS|FLAT)$",
        description="Frame type: LIGHT, DARK, BIAS, or FLAT.",
        examples=["LIGHT"],
    )
    job_id: str | None = Field(
        default=None,
        max_length=100,
        description="Optional job identifier for correlating exposures with external workflows.",
        examples=["night_run_001"],
    )


class ExposureStartResponse(BaseModel):
    """Response returned when an exposure start is accepted (HTTP 202)."""

    exposure_id: str = Field(description="Unique exposure identifier.", examples=["exp_7f3a2b1c"])
    exptime_s: float = Field(description="Requested exposure time in seconds.", examples=[5.0])
    started_at: datetime = Field(description="Exposure start timestamp (UTC ISO-8601).")
    estimated_done_at: datetime = Field(description="Estimated completion timestamp (UTC ISO-8601).")


class SequenceStartRequest(BaseModel):
    """Payload for starting a periodic exposure sequence."""

    exptime_s: float = Field(
        gt=0.001,
        le=3600,
        description="Exposure time per frame in seconds.",
        examples=[3.0],
    )
    count: int = Field(
        ge=0,
        description="Total number of exposures. `0` means indefinite (until `stop` is called).",
        examples=[10],
    )
    period_s: float = Field(
        gt=0,
        description="Interval in seconds between the start of consecutive exposures.",
        examples=[5.0],
    )
    drift_mode: str = Field(
        default="from_start",
        pattern="^(from_start|from_end)$",
        description=(
            "`from_start`: period is measured from the start of each exposure. "
            "`from_end`: period is measured from the end of each exposure."
        ),
        examples=["from_start"],
    )
    job_id: str | None = Field(
        default=None,
        max_length=100,
        description="Optional job identifier.",
        examples=["sequence_night_001"],
    )


class SequenceStartResponse(BaseModel):
    """Response returned when a sequence start is accepted (HTTP 202)."""

    sequence_id: str = Field(description="Unique sequence identifier.", examples=["seq_9a4c1d2e"])
    exptime_s: float = Field(description="Exposure time per frame in seconds.", examples=[3.0])
    count: int = Field(description="Number of scheduled exposures (0 = indefinite).", examples=[10])
    period_s: float = Field(description="Interval between exposures in seconds.", examples=[5.0])
    started_at: datetime = Field(description="Sequence start timestamp (UTC ISO-8601).")


class SequenceStopRequest(BaseModel):
    """Payload for stopping an in-progress sequence."""

    sequence_id: str = Field(
        description="Identifier of the sequence to stop.",
        examples=["seq_9a4c1d2e"],
    )


class CaptureStatusResponse(BaseModel):
    """Status of the most recent capture."""

    capture_id: str | None = Field(default=None, description="Active or last exposure ID.", examples=["exp_7f3a2b1c"])
    status: str = Field(description="Capture status: `NONE`, `RUNNING`, `SUCCESS`, `FAILED`, or `ABORTED`.", examples=["SUCCESS"])
    file_path: str | None = Field(default=None, description="Path to the generated FITS file.", examples=["/opt/cavex/data/cavex_0001.fits"])
    sidecar_written: bool = Field(default=False, description="`true` if the metadata sidecar file was written successfully.")
    error_message: str | None = Field(default=None, description="Error message when `status` is `FAILED`.")
