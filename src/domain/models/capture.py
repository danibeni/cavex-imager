"""Capture entity and related domain enums."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class CaptureStatus(str, Enum):
    """Capture lifecycle states."""

    PENDING = "pending"
    EXPOSING = "exposing"
    READING = "reading"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


class FrameType(str, Enum):
    """Supported frame types."""

    LIGHT = "LIGHT"
    DARK = "DARK"
    BIAS = "BIAS"
    FLAT = "FLAT"


@dataclass
class Capture:
    """Represents a single camera capture request and outcome."""

    id: str
    lease_id: str
    exptime_s: float
    frame_type: FrameType
    binning: tuple[int, int]
    status: CaptureStatus
    job_id: str | None = None
    sequence_id: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    file_path: str | None = None
    error_message: str | None = None
    sidecar_written: bool = False

    def __post_init__(self) -> None:
        """Validate capture invariants after construction."""
        if not isinstance(self.status, CaptureStatus):
            raise ValueError("status must be an instance of CaptureStatus")
        if not isinstance(self.frame_type, FrameType):
            raise ValueError("frame_type must be an instance of FrameType")
        if self.exptime_s <= 0:
            raise ValueError("exptime_s must be greater than zero")
        if len(self.binning) != 2 or self.binning[0] <= 0 or self.binning[1] <= 0:
            raise ValueError("binning must be a tuple of two positive integers")

    @staticmethod
    def _now_utc() -> datetime:
        return datetime.now(timezone.utc)

    def mark_as_exposing(self) -> None:
        """Transition capture to exposing state."""
        self.status = CaptureStatus.EXPOSING
        self.started_at = self._now_utc()

    def mark_as_completed(self, file_path: str, sidecar_ok: bool) -> None:
        """Transition capture to completed state.

        Args:
            file_path: Output FITS file path.
            sidecar_ok: Whether sidecar writing succeeded.
        """
        self.status = CaptureStatus.COMPLETED
        self.completed_at = self._now_utc()
        self.file_path = file_path
        self.sidecar_written = sidecar_ok
        self.error_message = None

    def mark_as_failed(self, error: str) -> None:
        """Transition capture to failed state.

        Args:
            error: Failure reason message.
        """
        self.status = CaptureStatus.FAILED
        self.completed_at = self._now_utc()
        self.error_message = error

    def mark_as_aborted(self) -> None:
        """Transition capture to aborted state."""
        self.status = CaptureStatus.ABORTED
        self.completed_at = self._now_utc()
