"""Domain exceptions for capture operations."""

from __future__ import annotations


class CaptureError(Exception):
    """Base exception for capture-related errors."""


class CaptureTimeout(CaptureError):  # noqa: N818
    """Raised when capture exceeds timeout constraints."""


class HardwareError(CaptureError):
    """Raised when hardware operation fails."""


class InvalidCaptureParameters(CaptureError):  # noqa: N818
    """Raised when capture parameters are invalid."""


class ExposureInProgress(CaptureError):  # noqa: N818
    """Raised when trying to start exposure while one is active."""


class NoActiveExposure(CaptureError):  # noqa: N818
    """Raised when trying to abort without active exposure."""
