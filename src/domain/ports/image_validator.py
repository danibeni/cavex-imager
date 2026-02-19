"""Image validation port definition."""

from __future__ import annotations

from abc import ABC, abstractmethod


class IImageValidator(ABC):
    """Port for validating captured image files."""

    @abstractmethod
    def validate(self, file_path: str) -> tuple[bool, str | None]:
        """Check whether a captured image file is valid.

        Args:
            file_path: Absolute path to the image file.

        Returns:
            Tuple of (is_valid, error_message). error_message is None when valid.
        """
