"""Image metadata value object matching sidecar JSON spec."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class ImageMetadata:
    """Immutable metadata generated for a capture sidecar JSON."""

    # File identification
    filename: str
    timestamp_utc: datetime

    # Device information
    device_name: str
    device_model: str

    # Acquisition parameters
    exptime_s: float
    frame_type: str
    gain: int | None
    offset: int | None
    binning: tuple[int, int]
    roi: tuple[int, int, int, int]

    # Thermal data
    ccd_temp_c: float
    cooler_power_pct: float

    # Context
    lease_owner: str
    job_id: str | None
    sequence_id: str | None
    exposure_id: str

    # Service metadata
    service_version: str
