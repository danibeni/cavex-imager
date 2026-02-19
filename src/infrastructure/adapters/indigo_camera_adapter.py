"""Event-driven INDIGO camera adapter with local state cache.

Subscribes to INDIGO property callbacks (define/update/delete) on a specific
device and maintains an internal cache of camera state.  No polling is used;
all state changes are pushed by the INDIGO server.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import structlog

from src.domain.ports.camera_device import CameraStateSnapshot, ICameraDevice
from src.infrastructure.adapters.indigo_client import (
    IndigoClient,
    IndigoProperty,
)

logger = structlog.get_logger(__name__)

# Delay before device auto-reconnect attempt (seconds)
_DEVICE_AUTOCONNECT_DELAY = 5

# INDIGO property names
_PROP_CONNECTION = "CONNECTION"
_PROP_CCD_EXPOSURE = "CCD_EXPOSURE"
_PROP_CCD_ABORT = "CCD_ABORT_EXPOSURE"
_PROP_CCD_TEMP = "CCD_TEMPERATURE"
_PROP_CCD_COOLER = "CCD_COOLER"
_PROP_CCD_COOLER_POWER = "CCD_COOLER_POWER"
_PROP_CCD_BIN = "CCD_BIN"
_PROP_CCD_FRAME = "CCD_FRAME"
_PROP_CCD_GAIN = "CCD_GAIN"
_PROP_CCD_OFFSET = "CCD_OFFSET"
_PROP_CCD_LOCAL_MODE = "CCD_LOCAL_MODE"
_PROP_CCD_UPLOAD_MODE = "CCD_UPLOAD_MODE"
_PROP_CCD_IMAGE = "CCD_IMAGE"
_PROP_CCD_IMAGE_FILE = "CCD_IMAGE_FILE"
_PROP_CCD_FRAME_TYPE = "CCD_FRAME_TYPE"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class IndigoCameraAdapter(ICameraDevice):
    """ICameraDevice backed by INDIGO protocol using event-driven callbacks.

    The INDIGO server pushes property definitions (defXXXVector), updates
    (setXXXVector) and deletions (deleteProperty). This adapter registers
    callbacks for a specific device and keeps a local state cache in sync.
    No polling loops are used.
    """

    def __init__(self, device_name: str, auto_connect: bool = True) -> None:
        """Initialize adapter.

        Args:
            device_name: INDIGO device identifier (e.g. 'CCD Imager Simulator').
            auto_connect: Auto-connect device when CONNECTION shows disconnected.
        """
        self._device_name = device_name
        self._client = IndigoClient()
        self._auto_connect = auto_connect
        self._autoconnect_task: asyncio.Task[None] | None = None
        logger.info("adapter_initialized", device_name=device_name)

        # Local state cache
        self._connected = False
        self._device_connected = False
        self._exposure_state = "IDLE"
        self._exposure_value = 0.0
        self._exposure_target = 0.0
        self._ccd_temp_c = 0.0
        self._cooler_target_c = 0.0
        self._cooler_power_pct = 0.0
        self._cooler_on = False
        self._cooler_state = "OFF"
        self._bin_x = 1
        self._bin_y = 1
        self._roi = (0, 0, 0, 0)
        self._gain: int | None = None
        self._offset: int | None = None
        self._last_image_path: str | None = None
        self._local_mode_dir: str | None = None
        self._last_update = _now_utc()

        # Event handlers
        self._exposure_state_handlers: list[Callable[[str, str], Awaitable[None]]] = []
        self._image_received_handlers: list[Callable[[str], Awaitable[None]]] = []
        self._temperature_changed_handlers: list[Callable[[float, float], Awaitable[None]]] = []
        self._cooler_state_changed_handlers: list[Callable[[str, str], Awaitable[None]]] = []

    # ── Connection Lifecycle ─────────────────────────────────────────

    async def connect(self, host: str, port: int) -> None:
        """Connect to INDIGO server and register event callbacks.

        Args:
            host: INDIGO server host.
            port: INDIGO server port (default 7624).
        """
        # Register callbacks BEFORE connecting so we don't miss the def flood
        dev = self._device_name
        self._client.on_define(self._on_property_defined, device=dev)
        self._client.on_update(self._on_property_updated, device=dev)
        self._client.on_delete(self._on_property_deleted, device=dev)

        await self._client.connect(host, port)
        self._connected = True
        logger.info("adapter_connected", device=dev, host=host, port=port)

    async def disconnect(self) -> None:
        """Disconnect device and close INDIGO client."""
        self._cancel_autoconnect()
        await self._client.disconnect()
        self._connected = False
        self._device_connected = False
        logger.info("adapter_disconnected", device=self._device_name)

    async def connect_device(self) -> None:
        """Connect camera device via INDIGO CONNECTION switch."""
        await self._client.send_switch(
            self._device_name, _PROP_CONNECTION, {"CONNECTED": True, "DISCONNECTED": False}
        )
        logger.info("device_connect_requested", device=self._device_name)

    async def disconnect_device(self) -> None:
        """Disconnect camera device."""
        await self._client.send_switch(
            self._device_name, _PROP_CONNECTION, {"CONNECTED": False, "DISCONNECTED": True}
        )
        logger.info("device_disconnect_requested", device=self._device_name)

    # ── Configuration Commands ───────────────────────────────────────

    async def set_config(self, config: dict[str, Any]) -> None:
        """Apply acquisition configuration to camera.

        Args:
            config: Dict with optional keys: bin_x, bin_y, roi,
                    gain, offset, cooler_enabled, cooler_target_c.
        """
        if "bin_x" in config or "bin_y" in config:
            bx = config.get("bin_x", self._bin_x)
            by = config.get("bin_y", self._bin_y)
            await self._client.send_number(
                self._device_name, _PROP_CCD_BIN, {"HORIZONTAL": bx, "VERTICAL": by}
            )

        if "roi" in config:
            roi = config["roi"]
            await self._client.send_number(
                self._device_name,
                _PROP_CCD_FRAME,
                {"LEFT": roi[0], "TOP": roi[1], "WIDTH": roi[2], "HEIGHT": roi[3]},
            )

        if "gain" in config:
            await self._client.send_number(
                self._device_name, _PROP_CCD_GAIN, {"GAIN": config["gain"]}
            )

        if "offset" in config:
            await self._client.send_number(
                self._device_name, _PROP_CCD_OFFSET, {"OFFSET": config["offset"]}
            )

        if "cooler_enabled" in config:
            on = bool(config["cooler_enabled"])
            await self._client.send_switch(
                self._device_name, _PROP_CCD_COOLER, {"ON": on, "OFF": not on}
            )

        if "cooler_target_c" in config:
            await self._client.send_number(
                self._device_name, _PROP_CCD_TEMP, {"TEMPERATURE": config["cooler_target_c"]}
            )

        logger.info("config_applied", device=self._device_name, config=config)

    async def set_local_mode(self, directory: str, prefix: str) -> None:
        """Configure INDIGO local save mode.

        Args:
            directory: Storage directory path (host-side).
            prefix: Filename prefix for saved images.
        """
        await self._client.send_switch(
            self._device_name,
            _PROP_CCD_UPLOAD_MODE,
            {"LOCAL": True, "CLIENT": False, "BOTH": False},
        )
        await self._client.send_text(
            self._device_name, _PROP_CCD_LOCAL_MODE, {"DIR": directory, "PREFIX": prefix}
        )
        self._local_mode_dir = directory
        logger.info("local_mode_set", device=self._device_name, dir=directory, prefix=prefix)

    async def start_exposure(self, exptime_s: float) -> None:
        """Start a camera exposure.

        Args:
            exptime_s: Exposure time in seconds.
        """
        await self._client.send_number(
            self._device_name, _PROP_CCD_EXPOSURE, {"EXPOSURE": exptime_s}
        )
        self._exposure_target = exptime_s
        logger.info("exposure_started", device=self._device_name, exptime_s=exptime_s)

    async def abort_exposure(self) -> None:
        """Abort active camera exposure."""
        await self._client.send_switch(
            self._device_name, _PROP_CCD_ABORT, {"ABORT_EXPOSURE": True}
        )
        logger.info("exposure_abort_requested", device=self._device_name)

    def get_state(self) -> CameraStateSnapshot:
        """Return current camera state from local cache."""
        return CameraStateSnapshot(
            device_name=self._device_name,
            connected=self._device_connected,
            indigo_connected=self._client.is_connected,
            exposure_state=self._exposure_state,
            exposure_value=self._exposure_value,
            exposure_target=self._exposure_target,
            ccd_temp_c=self._ccd_temp_c,
            cooler_target_c=self._cooler_target_c,
            cooler_power_pct=self._cooler_power_pct,
            cooler_on=self._cooler_on,
            cooler_state=self._cooler_state,
            bin_x=self._bin_x,
            bin_y=self._bin_y,
            roi=self._roi,
            gain=self._gain,
            offset=self._offset,
            last_image_path=self._last_image_path,
            local_mode_dir=self._local_mode_dir,
            last_update=self._last_update,
        )

    def subscribe_to_exposure_events(
        self,
        on_state_changed: Callable[[str, str], Awaitable[None]] | None = None,
        on_image_received: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        """Register exposure event handlers."""
        if on_state_changed is not None:
            self._exposure_state_handlers.append(on_state_changed)
        if on_image_received is not None:
            self._image_received_handlers.append(on_image_received)

    def subscribe_to_thermal_events(
        self,
        on_temperature_changed: Callable[[float, float], Awaitable[None]] | None = None,
        on_cooler_state_changed: Callable[[str, str], Awaitable[None]] | None = None,
    ) -> None:
        """Register thermal event handlers."""
        if on_temperature_changed is not None:
            self._temperature_changed_handlers.append(on_temperature_changed)
        if on_cooler_state_changed is not None:
            self._cooler_state_changed_handlers.append(on_cooler_state_changed)

    # ── INDIGO Event Callbacks (event-driven, no polling) ────────────

    async def _on_property_defined(self, prop: IndigoProperty) -> None:
        """Handle defXXXVector: property announced by the server.

        Processes initial values (def carries current state) and triggers
        auto-connect / BLOB enablement when relevant properties appear.
        """
        self._apply_property_to_cache(prop)

        # Auto-connect device when CONNECTION shows disconnected
        if prop.name == _PROP_CONNECTION and self._auto_connect:
            connected = prop.items.get("CONNECTED")
            if connected and not connected.value_switch:
                await self.connect_device()

        # Enable BLOB URL transfer when CCD_IMAGE property appears
        if prop.name == _PROP_CCD_IMAGE:
            await self._client.enable_blob(self._device_name, _PROP_CCD_IMAGE, "URL")

    async def _on_property_updated(self, prop: IndigoProperty) -> None:
        """Handle setXXXVector: property value update pushed by the server."""
        previous_exposure_state = self._exposure_state
        previous_temp = self._ccd_temp_c
        previous_target = self._cooler_target_c
        previous_cooler_state = self._cooler_state
        previous_image_path = self._last_image_path

        self._apply_property_to_cache(prop)

        if (
            prop.name == _PROP_CCD_EXPOSURE
            and previous_exposure_state != self._exposure_state
            and self._exposure_state_handlers
        ):
            await self._notify_exposure_state_changed(previous_exposure_state, self._exposure_state)

        if (
            prop.name == _PROP_CCD_IMAGE_FILE
            and self._last_image_path
            and self._last_image_path != previous_image_path
            and os.path.isfile(self._last_image_path)
            and self._image_received_handlers
        ):
            await self._notify_image_received(self._last_image_path)

        if (
            prop.name == _PROP_CCD_TEMP
            and (previous_temp != self._ccd_temp_c or previous_target != self._cooler_target_c)
            and self._temperature_changed_handlers
        ):
            await self._notify_temperature_changed(self._ccd_temp_c, self._cooler_target_c)

        if (
            prop.name in {_PROP_CCD_COOLER, _PROP_CCD_COOLER_POWER, _PROP_CCD_TEMP}
            and previous_cooler_state != self._cooler_state
            and self._cooler_state_changed_handlers
        ):
            await self._notify_cooler_state_changed(previous_cooler_state, self._cooler_state)

        # Schedule device auto-reconnect if CONNECTION went OFF
        if prop.name == _PROP_CONNECTION and self._auto_connect:
            connected = prop.items.get("CONNECTED")
            if connected and not connected.value_switch:
                self._schedule_autoconnect()

    async def _on_property_deleted(self, device: str, property_name: str) -> None:
        """Handle deleteProperty: device removed or property unavailable."""
        if not property_name:
            # All device properties deleted — device fully removed
            logger.warning("device_removed", device=device)
            self._device_connected = False
            self._reset_cache()
        else:
            logger.debug("property_deleted", device=device, property=property_name)

    # ── Cache Update from Typed Properties ───────────────────────────

    def _apply_property_to_cache(self, prop: IndigoProperty) -> None:
        """Update local camera state from a typed IndigoProperty."""
        self._last_update = _now_utc()

        if prop.name == _PROP_CONNECTION:
            item = prop.items.get("CONNECTED")
            if item is not None:
                self._device_connected = item.value_switch

        elif prop.name == _PROP_CCD_EXPOSURE:
            if prop.state is not None:
                self._exposure_state = prop.state.value
            item = prop.items.get("EXPOSURE")
            if item is not None:
                self._exposure_value = item.value_number
                if item.target_number > 0:
                    self._exposure_target = item.target_number

        elif prop.name == _PROP_CCD_TEMP:
            item = prop.items.get("TEMPERATURE")
            if item is not None:
                self._ccd_temp_c = item.value_number
                self._cooler_target_c = item.target_number

        elif prop.name == _PROP_CCD_COOLER:
            item = prop.items.get("ON")
            if item is not None:
                self._cooler_on = item.value_switch
            self._derive_cooler_state()

        elif prop.name == _PROP_CCD_COOLER_POWER:
            item = prop.items.get("POWER")
            if item is not None:
                self._cooler_power_pct = item.value_number
            self._derive_cooler_state()

        elif prop.name == _PROP_CCD_BIN:
            h = prop.items.get("HORIZONTAL")
            v = prop.items.get("VERTICAL")
            if h is not None:
                self._bin_x = int(h.value_number)
            if v is not None:
                self._bin_y = int(v.value_number)

        elif prop.name == _PROP_CCD_FRAME:
            left = prop.items.get("LEFT")
            top = prop.items.get("TOP")
            width = prop.items.get("WIDTH")
            height = prop.items.get("HEIGHT")
            self._roi = (
                int(left.value_number) if left else 0,
                int(top.value_number) if top else 0,
                int(width.value_number) if width else 0,
                int(height.value_number) if height else 0,
            )

        elif prop.name == _PROP_CCD_GAIN:
            item = prop.items.get("GAIN")
            if item is not None:
                self._gain = int(item.value_number)

        elif prop.name == _PROP_CCD_OFFSET:
            item = prop.items.get("OFFSET")
            if item is not None:
                self._offset = int(item.value_number)

        elif prop.name == _PROP_CCD_IMAGE:
            item = prop.items.get("IMAGE")
            if item is not None and item.value_blob_url:
                self._last_image_path = item.value_blob_url

        elif prop.name == _PROP_CCD_IMAGE_FILE:
            item = prop.items.get("FILE")
            if item is not None and item.value_text:
                self._last_image_path = item.value_text

        elif prop.name == _PROP_CCD_LOCAL_MODE:
            item = prop.items.get("DIR")
            if item is not None and item.value_text:
                self._local_mode_dir = item.value_text

    # ── Internal Helpers ─────────────────────────────────────────────

    def _derive_cooler_state(self) -> None:
        """Derive cooler state string from current readings."""
        if not self._cooler_on:
            self._cooler_state = "OFF"
        elif self._cooler_power_pct >= 99:
            self._cooler_state = "ALARM"
        elif abs(self._ccd_temp_c - self._cooler_target_c) < 1.0:
            self._cooler_state = "STABLE"
        else:
            self._cooler_state = "RAMPING"

    def _reset_cache(self) -> None:
        """Reset all cached values to defaults (device removed/disconnected)."""
        self._exposure_state = "IDLE"
        self._exposure_value = 0.0
        self._exposure_target = 0.0
        self._ccd_temp_c = 0.0
        self._cooler_target_c = 0.0
        self._cooler_power_pct = 0.0
        self._cooler_on = False
        self._cooler_state = "OFF"
        self._bin_x = 1
        self._bin_y = 1
        self._roi = (0, 0, 0, 0)
        self._gain = None
        self._offset = None
        self._last_image_path = None
        self._local_mode_dir = None

    def _schedule_autoconnect(self) -> None:
        """Schedule a delayed device auto-reconnect attempt."""
        if self._autoconnect_task is not None and not self._autoconnect_task.done():
            return  # Already pending
        self._autoconnect_task = asyncio.create_task(self._delayed_autoconnect())

    async def _delayed_autoconnect(self) -> None:
        """Wait then attempt to reconnect the device."""
        try:
            await asyncio.sleep(_DEVICE_AUTOCONNECT_DELAY)
            if self._connected and not self._device_connected and self._auto_connect:
                logger.info("device_autoconnect_attempt", device=self._device_name)
                await self.connect_device()
        except asyncio.CancelledError:
            return

    def _cancel_autoconnect(self) -> None:
        """Cancel any pending autoconnect task."""
        if self._autoconnect_task is not None:
            self._autoconnect_task.cancel()
            self._autoconnect_task = None

    async def _notify_exposure_state_changed(self, previous: str, current: str) -> None:
        """Invoke subscribed handlers for exposure state changes."""
        for handler in self._exposure_state_handlers:
            try:
                await handler(previous, current)
            except Exception as error:
                logger.exception("exposure_state_handler_failed", error=str(error))

    async def _notify_image_received(self, file_path: str) -> None:
        """Invoke subscribed handlers when image path is available."""
        for handler in self._image_received_handlers:
            try:
                await handler(file_path)
            except Exception as error:
                logger.exception("image_received_handler_failed", error=str(error))

    async def _notify_temperature_changed(self, current_temp: float, target_temp: float) -> None:
        """Invoke subscribed handlers for temperature changes."""
        for handler in self._temperature_changed_handlers:
            try:
                await handler(current_temp, target_temp)
            except Exception as error:
                logger.exception("temperature_handler_failed", error=str(error))

    async def _notify_cooler_state_changed(self, previous: str, current: str) -> None:
        """Invoke subscribed handlers for cooler state changes."""
        for handler in self._cooler_state_changed_handlers:
            try:
                await handler(previous, current)
            except Exception as error:
                logger.exception("cooler_state_handler_failed", error=str(error))
