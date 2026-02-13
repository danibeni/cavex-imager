"""Domain events related to capture lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class CaptureStarted:
    """Event emitted when a capture starts."""

    capture_id: str
    lease_id: str
    exptime_s: float
    job_id: str | None
    timestamp: datetime


@dataclass(frozen=True)
class CaptureCompleted:
    """Event emitted when a capture completes."""

    capture_id: str
    job_id: str | None
    exptime_s: float
    file_path: str
    sidecar_written: bool
    ccd_temp_c: float
    timestamp: datetime


@dataclass(frozen=True)
class ImageReady:
    """Event emitted when image and sidecar are ready for analysis."""

    capture_id: str
    image_path: str
    sidecar_path: str
    timestamp: datetime


@dataclass(frozen=True)
class CaptureFailed:
    """Event emitted when a capture fails."""

    capture_id: str
    error_message: str
    timestamp: datetime


@dataclass(frozen=True)
class CaptureAborted:
    """Event emitted when a capture is aborted."""

    capture_id: str
    timestamp: datetime
