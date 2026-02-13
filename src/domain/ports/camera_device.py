"""Camera device port definitions (ICameraPort in architecture spec)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class CameraStateSnapshot:
    """Complete camera state read from local cache."""

    device_name: str
    connected: bool
    indigo_connected: bool

    # Exposure
    exposure_state: str  # IDLE / BUSY / OK / ALERT
    exposure_value: float
    exposure_target: float

    # Thermal
    ccd_temp_c: float
    cooler_target_c: float
    cooler_power_pct: float
    cooler_on: bool
    cooler_state: str  # OFF / RAMPING / STABLE / ALARM

    # Configuration
    bin_x: int
    bin_y: int
    roi: tuple[int, int, int, int]
    gain: int | None
    offset: int | None

    # Storage
    last_image_path: str | None
    local_mode_dir: str | None

    last_update: datetime


class ICameraDevice(ABC):
    """Interface for camera device operations."""

    @abstractmethod
    async def connect(self, host: str, port: int) -> None:
        """Connect to INDIGO server.

        Args:
            host: INDIGO server host.
            port: INDIGO server port.
        """

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from INDIGO server."""

    @abstractmethod
    async def connect_device(self) -> None:
        """Connect the camera device via INDIGO CONNECTION property."""

    @abstractmethod
    async def disconnect_device(self) -> None:
        """Disconnect the camera device."""

    @abstractmethod
    async def set_config(self, config: dict[str, Any]) -> None:
        """Apply acquisition configuration to camera.

        Args:
            config: Dict with optional keys: bin_x, bin_y, roi,
                    gain, offset, cooler_enabled, cooler_target_c.
        """

    @abstractmethod
    async def set_local_mode(self, directory: str, prefix: str) -> None:
        """Configure INDIGO local save mode.

        Args:
            directory: Storage directory path (host-side).
            prefix: Filename prefix for saved images.
        """

    @abstractmethod
    async def start_exposure(self, exptime_s: float) -> None:
        """Start a camera exposure.

        Args:
            exptime_s: Exposure time in seconds.
        """

    @abstractmethod
    async def abort_exposure(self) -> None:
        """Abort exposure currently in progress."""

    @abstractmethod
    def get_state(self) -> CameraStateSnapshot:
        """Return current camera state from local cache.

        Returns:
            Snapshot of current camera state.
        """
