"""INDIGO-based camera adapter with local state cache."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import structlog

from src.domain.ports.camera_device import CameraStateSnapshot, ICameraDevice
from src.infrastructure.adapters.indigo_client import IndigoClient

logger = structlog.get_logger(__name__)

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
_PROP_CCD_FRAME_TYPE = "CCD_FRAME_TYPE"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class IndigoCameraAdapter(ICameraDevice):
    """ICameraDevice implementation backed by INDIGO protocol."""

    def __init__(self, device_name: str) -> None:
        """Initialize adapter with device name.

        Args:
            device_name: INDIGO device identifier.
        """
        self._device_name = device_name
        self._client = IndigoClient()
        self._event_task: asyncio.Task[None] | None = None

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

    async def connect(self, host: str, port: int) -> None:
        """Connect to INDIGO server and start cache updater."""
        await self._client.connect(host, port)
        self._connected = True
        self._event_task = asyncio.create_task(self._cache_updater())
        logger.info("adapter_connected", device=self._device_name, host=host, port=port)

    async def disconnect(self) -> None:
        """Disconnect device and close INDIGO client."""
        if self._event_task is not None:
            self._event_task.cancel()
            try:
                await self._event_task
            except asyncio.CancelledError:
                pass
            self._event_task = None

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
        """Configure INDIGO local save mode."""
        # Set upload mode to LOCAL
        await self._client.send_switch(
            self._device_name,
            _PROP_CCD_UPLOAD_MODE,
            {"LOCAL": True, "CLIENT": False, "BOTH": False},
        )
        # Set directory and prefix
        await self._client.send_text(
            self._device_name, _PROP_CCD_LOCAL_MODE, {"DIR": directory, "PREFIX": prefix}
        )
        self._local_mode_dir = directory
        logger.info("local_mode_set", device=self._device_name, dir=directory, prefix=prefix)

    async def start_exposure(self, exptime_s: float) -> None:
        """Start a camera exposure."""
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
            indigo_connected=self._connected,
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

    # ── Cache updater from INDIGO events ────────────────────────────

    async def _cache_updater(self) -> None:
        """Background task that updates local cache from INDIGO events."""
        try:
            async for event in self._client.event_stream():
                device = event.get("device")
                if device and device != self._device_name:
                    continue
                self._update_cache(event)
        except asyncio.CancelledError:
            return

    def _update_cache(self, event: dict[str, Any]) -> None:
        """Update local cache fields from an INDIGO event.

        Args:
            event: Parsed INDIGO event dictionary.
        """
        prop = event.get("property", "")
        values = event.get("values", {})
        state = event.get("state", "")
        self._last_update = _now_utc()

        if prop == _PROP_CONNECTION:
            connected_val = values.get("CONNECTED", "").lower()
            self._device_connected = connected_val in ("on", "true", "1")

        elif prop == _PROP_CCD_EXPOSURE:
            self._exposure_state = state or "IDLE"
            exp_val = values.get("EXPOSURE", "")
            if exp_val:
                try:
                    self._exposure_value = float(exp_val)
                except ValueError:
                    pass

        elif prop == _PROP_CCD_TEMP:
            temp = values.get("TEMPERATURE", "")
            if temp:
                try:
                    self._ccd_temp_c = float(temp)
                except ValueError:
                    pass
            target = values.get("TARGET", values.get("TEMPERATURE", ""))
            if target and state == "Ok":
                try:
                    self._cooler_target_c = float(target)
                except ValueError:
                    pass

        elif prop == _PROP_CCD_COOLER:
            self._cooler_on = values.get("ON", "").lower() in ("on", "true", "1")

        elif prop == _PROP_CCD_COOLER_POWER:
            pwr = values.get("POWER", "")
            if pwr:
                try:
                    self._cooler_power_pct = float(pwr)
                except ValueError:
                    pass
            # Derive cooler state
            if not self._cooler_on:
                self._cooler_state = "OFF"
            elif self._cooler_power_pct >= 99:
                self._cooler_state = "ALARM"
            elif abs(self._ccd_temp_c - self._cooler_target_c) < 1.0:
                self._cooler_state = "STABLE"
            else:
                self._cooler_state = "RAMPING"

        elif prop == _PROP_CCD_BIN:
            h = values.get("HORIZONTAL", "")
            v = values.get("VERTICAL", "")
            if h:
                try:
                    self._bin_x = int(float(h))
                except ValueError:
                    pass
            if v:
                try:
                    self._bin_y = int(float(v))
                except ValueError:
                    pass

        elif prop == _PROP_CCD_FRAME:
            try:
                self._roi = (
                    int(float(values.get("LEFT", "0"))),
                    int(float(values.get("TOP", "0"))),
                    int(float(values.get("WIDTH", "0"))),
                    int(float(values.get("HEIGHT", "0"))),
                )
            except ValueError:
                pass

        elif prop == _PROP_CCD_GAIN:
            g = values.get("GAIN", "")
            if g:
                try:
                    self._gain = int(float(g))
                except ValueError:
                    pass

        elif prop == _PROP_CCD_OFFSET:
            o = values.get("OFFSET", "")
            if o:
                try:
                    self._offset = int(float(o))
                except ValueError:
                    pass

        elif prop == _PROP_CCD_IMAGE:
            # INDIGO reports the filename via the BLOB property
            img_val = values.get("IMAGE", "")
            if img_val:
                self._last_image_path = img_val

        elif prop == _PROP_CCD_LOCAL_MODE:
            d = values.get("DIR", "")
            if d:
                self._local_mode_dir = d
