"""Storage port definitions (IFilesystemPort in architecture spec)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from src.domain.models.image_metadata import ImageMetadata


@dataclass
class StorageValidation:
    """Result of path validation."""

    valid: bool
    writable: bool
    disk_free_gb: float
    error: str | None = None


class IStorage(ABC):
    """Interface for filesystem and metadata persistence operations."""

    @abstractmethod
    async def validate_path(self, path: str) -> StorageValidation:
        """Validate storage path exists, is writable, and has space.

        Args:
            path: Directory path to validate.

        Returns:
            Validation result with disk space info.
        """

    @abstractmethod
    async def check_disk_space(self, path: str) -> float:
        """Return free disk space in GB for given path.

        Args:
            path: Directory path to check.

        Returns:
            Free space in gigabytes.
        """

    @abstractmethod
    async def write_sidecar(self, fits_path: str, metadata: ImageMetadata) -> bool:
        """Write sidecar JSON metadata associated with a FITS image.

        Uses atomic write (tmp file + rename) to prevent partial reads.

        Args:
            fits_path: FITS file path.
            metadata: Structured image metadata.

        Returns:
            True if sidecar writing succeeded, False otherwise.
        """
