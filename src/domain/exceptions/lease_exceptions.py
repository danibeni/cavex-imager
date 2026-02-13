"""Domain exceptions for lease operations."""

from __future__ import annotations


class LeaseError(Exception):
    """Base exception for lease-related errors."""


class LeaseNotAvailable(LeaseError):  # noqa: N818
    """Raised when lease cannot be acquired."""


class LeaseConflict(LeaseError):  # noqa: N818
    """Raised when lease conflicts with higher-priority holder."""


class LeaseExpiredError(LeaseError):  # noqa: N818
    """Raised when requested lease is expired."""


class LeaseNotFound(LeaseError):  # noqa: N818
    """Raised when requested lease does not exist."""
