"""Unit tests for IndigoCameraAdapter event-driven callbacks."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.infrastructure.adapters.indigo_camera_adapter import IndigoCameraAdapter
from src.infrastructure.adapters.indigo_client import (
    IndigoItem,
    IndigoProperty,
    PropertyPerm,
    PropertyState,
    PropertyType,
)


def _make_adapter(device: str = "TestCCD", auto_connect: bool = False) -> IndigoCameraAdapter:
    """Create an adapter with a mocked IndigoClient for isolated testing."""
    adapter = IndigoCameraAdapter(device_name=device, auto_connect=auto_connect)
    adapter._client = MagicMock()
    adapter._client.is_connected = True
    adapter._client.send_switch = AsyncMock()
    adapter._client.send_number = AsyncMock()
    adapter._client.send_text = AsyncMock()
    adapter._client.enable_blob = AsyncMock()
    adapter._connected = True
    return adapter


# ── Property Define Callback Tests ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_on_define_connection_auto_connects_when_disconnected() -> None:
    """Test adapter sends CONNECTION=On when device shows disconnected."""
    adapter = _make_adapter(auto_connect=True)
    prop = IndigoProperty(
        device="TestCCD", name="CONNECTION", type=PropertyType.SWITCH,
        state=PropertyState.OK, perm=PropertyPerm.RW,
        items={
            "CONNECTED": IndigoItem(name="CONNECTED", value_switch=False),
            "DISCONNECTED": IndigoItem(name="DISCONNECTED", value_switch=True),
        },
    )

    await adapter._on_property_defined(prop)

    adapter._client.send_switch.assert_called_once_with(
        "TestCCD", "CONNECTION", {"CONNECTED": True, "DISCONNECTED": False}
    )


@pytest.mark.asyncio
async def test_on_define_connection_skips_autoconnect_when_already_connected() -> None:
    """Test adapter does NOT send CONNECTION when device already connected."""
    adapter = _make_adapter(auto_connect=True)
    prop = IndigoProperty(
        device="TestCCD", name="CONNECTION", type=PropertyType.SWITCH,
        state=PropertyState.OK,
        items={
            "CONNECTED": IndigoItem(name="CONNECTED", value_switch=True),
            "DISCONNECTED": IndigoItem(name="DISCONNECTED", value_switch=False),
        },
    )

    await adapter._on_property_defined(prop)

    adapter._client.send_switch.assert_not_called()


@pytest.mark.asyncio
async def test_on_define_connection_skips_autoconnect_when_disabled() -> None:
    """Test adapter does NOT auto-connect when auto_connect=False."""
    adapter = _make_adapter(auto_connect=False)
    prop = IndigoProperty(
        device="TestCCD", name="CONNECTION", type=PropertyType.SWITCH,
        state=PropertyState.OK,
        items={
            "CONNECTED": IndigoItem(name="CONNECTED", value_switch=False),
            "DISCONNECTED": IndigoItem(name="DISCONNECTED", value_switch=True),
        },
    )

    await adapter._on_property_defined(prop)

    adapter._client.send_switch.assert_not_called()


@pytest.mark.asyncio
async def test_on_define_ccd_image_enables_blob_url() -> None:
    """Test adapter sends enableBLOB URL when CCD_IMAGE property appears."""
    adapter = _make_adapter()
    prop = IndigoProperty(
        device="TestCCD", name="CCD_IMAGE", type=PropertyType.BLOB,
        state=PropertyState.OK, perm=PropertyPerm.RO,
    )

    await adapter._on_property_defined(prop)

    adapter._client.enable_blob.assert_called_once_with("TestCCD", "CCD_IMAGE", "URL")


@pytest.mark.asyncio
async def test_on_define_processes_initial_values() -> None:
    """Test adapter caches initial values carried by defXXXVector."""
    adapter = _make_adapter()
    prop = IndigoProperty(
        device="TestCCD", name="CCD_TEMPERATURE", type=PropertyType.NUMBER,
        state=PropertyState.IDLE,
        items={
            "TEMPERATURE": IndigoItem(
                name="TEMPERATURE", value_number=20.0, target_number=0.0
            ),
        },
    )

    await adapter._on_property_defined(prop)

    assert adapter._ccd_temp_c == pytest.approx(20.0)


# ── Property Update Callback Tests ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_on_update_connection_sets_device_connected() -> None:
    """Test adapter sets _device_connected=True on CONNECTION update."""
    adapter = _make_adapter()
    prop = IndigoProperty(
        device="TestCCD", name="CONNECTION", type=PropertyType.SWITCH,
        state=PropertyState.OK,
        items={
            "CONNECTED": IndigoItem(name="CONNECTED", value_switch=True),
            "DISCONNECTED": IndigoItem(name="DISCONNECTED", value_switch=False),
        },
    )

    await adapter._on_property_updated(prop)

    assert adapter._device_connected is True


@pytest.mark.asyncio
async def test_on_update_connection_disconnected_sets_false() -> None:
    """Test adapter sets _device_connected=False when CONNECTED goes Off."""
    adapter = _make_adapter()
    adapter._device_connected = True
    prop = IndigoProperty(
        device="TestCCD", name="CONNECTION", type=PropertyType.SWITCH,
        state=PropertyState.OK,
        items={
            "CONNECTED": IndigoItem(name="CONNECTED", value_switch=False),
            "DISCONNECTED": IndigoItem(name="DISCONNECTED", value_switch=True),
        },
    )

    await adapter._on_property_updated(prop)

    assert adapter._device_connected is False


@pytest.mark.asyncio
async def test_on_update_exposure_busy() -> None:
    """Test adapter caches exposure countdown with target from INDIGO."""
    adapter = _make_adapter()
    prop = IndigoProperty(
        device="TestCCD", name="CCD_EXPOSURE", type=PropertyType.NUMBER,
        state=PropertyState.BUSY,
        items={
            "EXPOSURE": IndigoItem(name="EXPOSURE", value_number=2.5, target_number=3.0),
        },
    )

    await adapter._on_property_updated(prop)

    assert adapter._exposure_state == "Busy"
    assert adapter._exposure_value == pytest.approx(2.5)
    assert adapter._exposure_target == pytest.approx(3.0)


@pytest.mark.asyncio
async def test_on_update_exposure_complete() -> None:
    """Test adapter caches state=Ok when exposure finishes."""
    adapter = _make_adapter()
    prop = IndigoProperty(
        device="TestCCD", name="CCD_EXPOSURE", type=PropertyType.NUMBER,
        state=PropertyState.OK,
        items={
            "EXPOSURE": IndigoItem(name="EXPOSURE", value_number=0.0, target_number=3.0),
        },
    )

    await adapter._on_property_updated(prop)

    assert adapter._exposure_state == "Ok"
    assert adapter._exposure_value == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_on_update_exposure_notifies_state_handlers() -> None:
    """Test adapter emits exposure state change callbacks."""
    adapter = _make_adapter()
    on_state_changed = AsyncMock()
    adapter.subscribe_to_exposure_events(on_state_changed=on_state_changed)
    adapter._exposure_state = "Busy"
    prop = IndigoProperty(
        device="TestCCD", name="CCD_EXPOSURE", type=PropertyType.NUMBER,
        state=PropertyState.OK,
        items={
            "EXPOSURE": IndigoItem(name="EXPOSURE", value_number=0.0, target_number=2.0),
        },
    )

    await adapter._on_property_updated(prop)

    on_state_changed.assert_awaited_once_with("Busy", "Ok")


@pytest.mark.asyncio
async def test_on_update_exposure_does_not_notify_when_unchanged() -> None:
    """Test state handler is not called if exposure state did not change."""
    adapter = _make_adapter()
    on_state_changed = AsyncMock()
    adapter.subscribe_to_exposure_events(on_state_changed=on_state_changed)
    adapter._exposure_state = "Busy"
    prop = IndigoProperty(
        device="TestCCD", name="CCD_EXPOSURE", type=PropertyType.NUMBER,
        state=PropertyState.BUSY,
        items={
            "EXPOSURE": IndigoItem(name="EXPOSURE", value_number=1.0, target_number=2.0),
        },
    )

    await adapter._on_property_updated(prop)

    on_state_changed.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_update_temperature_with_target() -> None:
    """Test adapter caches current temp and cooler target from target_number."""
    adapter = _make_adapter()
    prop = IndigoProperty(
        device="TestCCD", name="CCD_TEMPERATURE", type=PropertyType.NUMBER,
        state=PropertyState.BUSY,
        items={
            "TEMPERATURE": IndigoItem(
                name="TEMPERATURE", value_number=5.3, target_number=-20.0
            ),
        },
    )

    await adapter._on_property_updated(prop)

    assert adapter._ccd_temp_c == pytest.approx(5.3)
    assert adapter._cooler_target_c == pytest.approx(-20.0)


@pytest.mark.asyncio
async def test_on_update_temperature_notifies_handlers() -> None:
    """Test adapter emits temperature changed callbacks."""
    adapter = _make_adapter()
    on_temperature_changed = AsyncMock()
    adapter.subscribe_to_thermal_events(on_temperature_changed=on_temperature_changed)
    prop = IndigoProperty(
        device="TestCCD", name="CCD_TEMPERATURE", type=PropertyType.NUMBER,
        state=PropertyState.BUSY,
        items={
            "TEMPERATURE": IndigoItem(
                name="TEMPERATURE", value_number=5.3, target_number=-20.0
            ),
        },
    )

    await adapter._on_property_updated(prop)

    on_temperature_changed.assert_awaited_once_with(5.3, -20.0)


@pytest.mark.asyncio
async def test_on_update_cooler_switch() -> None:
    """Test adapter tracks cooler on/off switch."""
    adapter = _make_adapter()
    prop = IndigoProperty(
        device="TestCCD", name="CCD_COOLER", type=PropertyType.SWITCH,
        state=PropertyState.OK,
        items={"ON": IndigoItem(name="ON", value_switch=True)},
    )

    await adapter._on_property_updated(prop)

    assert adapter._cooler_on is True


@pytest.mark.asyncio
async def test_on_update_cooler_notifies_state_handlers() -> None:
    """Test adapter emits cooler state callbacks on transitions."""
    adapter = _make_adapter()
    on_cooler_state_changed = AsyncMock()
    adapter.subscribe_to_thermal_events(on_cooler_state_changed=on_cooler_state_changed)
    adapter._cooler_state = "OFF"
    prop = IndigoProperty(
        device="TestCCD", name="CCD_COOLER", type=PropertyType.SWITCH,
        state=PropertyState.OK,
        items={"ON": IndigoItem(name="ON", value_switch=True)},
    )

    await adapter._on_property_updated(prop)

    on_cooler_state_changed.assert_awaited_once_with("OFF", "STABLE")


@pytest.mark.asyncio
async def test_on_update_cooler_power_derives_state() -> None:
    """Test adapter derives cooler state from power and temperature."""
    adapter = _make_adapter()
    adapter._cooler_on = True
    adapter._ccd_temp_c = -20.0
    adapter._cooler_target_c = -20.0
    prop = IndigoProperty(
        device="TestCCD", name="CCD_COOLER_POWER", type=PropertyType.NUMBER,
        state=PropertyState.OK,
        items={"POWER": IndigoItem(name="POWER", value_number=45.0)},
    )

    await adapter._on_property_updated(prop)

    assert adapter._cooler_power_pct == pytest.approx(45.0)
    assert adapter._cooler_state == "STABLE"


@pytest.mark.asyncio
async def test_on_update_binning() -> None:
    """Test adapter caches binning values."""
    adapter = _make_adapter()
    prop = IndigoProperty(
        device="TestCCD", name="CCD_BIN", type=PropertyType.NUMBER,
        state=PropertyState.OK,
        items={
            "HORIZONTAL": IndigoItem(name="HORIZONTAL", value_number=2.0),
            "VERTICAL": IndigoItem(name="VERTICAL", value_number=2.0),
        },
    )

    await adapter._on_property_updated(prop)

    assert adapter._bin_x == 2
    assert adapter._bin_y == 2


@pytest.mark.asyncio
async def test_on_update_ccd_frame() -> None:
    """Test adapter caches ROI from CCD_FRAME property."""
    adapter = _make_adapter()
    prop = IndigoProperty(
        device="TestCCD", name="CCD_FRAME", type=PropertyType.NUMBER,
        state=PropertyState.OK,
        items={
            "LEFT": IndigoItem(name="LEFT", value_number=100.0),
            "TOP": IndigoItem(name="TOP", value_number=200.0),
            "WIDTH": IndigoItem(name="WIDTH", value_number=1920.0),
            "HEIGHT": IndigoItem(name="HEIGHT", value_number=1080.0),
        },
    )

    await adapter._on_property_updated(prop)

    assert adapter._roi == (100, 200, 1920, 1080)


@pytest.mark.asyncio
async def test_on_update_gain_and_offset() -> None:
    """Test adapter caches gain and offset values."""
    adapter = _make_adapter()

    await adapter._on_property_updated(IndigoProperty(
        device="TestCCD", name="CCD_GAIN", type=PropertyType.NUMBER,
        state=PropertyState.OK,
        items={"GAIN": IndigoItem(name="GAIN", value_number=120.0)},
    ))
    await adapter._on_property_updated(IndigoProperty(
        device="TestCCD", name="CCD_OFFSET", type=PropertyType.NUMBER,
        state=PropertyState.OK,
        items={"OFFSET": IndigoItem(name="OFFSET", value_number=10.0)},
    ))

    assert adapter._gain == 120
    assert adapter._offset == 10


@pytest.mark.asyncio
async def test_on_update_ccd_image_blob_url() -> None:
    """Test adapter caches BLOB URL from CCD_IMAGE update."""
    adapter = _make_adapter()
    prop = IndigoProperty(
        device="TestCCD", name="CCD_IMAGE", type=PropertyType.BLOB,
        state=PropertyState.OK,
        items={
            "IMAGE": IndigoItem(
                name="IMAGE",
                value_blob_url="http://localhost:7624/blob/0x123.fits",
            ),
        },
    )

    await adapter._on_property_updated(prop)

    assert adapter._last_image_path == "http://localhost:7624/blob/0x123.fits"


@pytest.mark.asyncio
async def test_on_update_ccd_image_file_local_path() -> None:
    """Test adapter caches local file path from CCD_IMAGE_FILE."""
    adapter = _make_adapter()
    prop = IndigoProperty(
        device="TestCCD", name="CCD_IMAGE_FILE", type=PropertyType.TEXT,
        state=PropertyState.OK,
        items={"FILE": IndigoItem(name="FILE", value_text="/data/capture_001.fits")},
    )

    await adapter._on_property_updated(prop)

    assert adapter._last_image_path == "/data/capture_001.fits"


@pytest.mark.asyncio
async def test_on_update_ccd_image_file_notifies_image_handlers(
    tmp_path: Path,
) -> None:
    """Test adapter emits image callback only for local existing files."""
    adapter = _make_adapter()
    on_image_received = AsyncMock()
    adapter.subscribe_to_exposure_events(on_image_received=on_image_received)
    image_path = tmp_path / "capture_001.fits"
    image_path.write_text("fits-placeholder", encoding="utf-8")
    prop = IndigoProperty(
        device="TestCCD", name="CCD_IMAGE_FILE", type=PropertyType.TEXT,
        state=PropertyState.OK,
        items={"FILE": IndigoItem(name="FILE", value_text=str(image_path))},
    )

    await adapter._on_property_updated(prop)

    on_image_received.assert_awaited_once_with(str(image_path))


@pytest.mark.asyncio
async def test_on_update_ccd_image_file_skips_missing_file_handler_call() -> None:
    """Test adapter does not emit image callback for missing files."""
    adapter = _make_adapter()
    on_image_received = AsyncMock()
    adapter.subscribe_to_exposure_events(on_image_received=on_image_received)
    prop = IndigoProperty(
        device="TestCCD", name="CCD_IMAGE_FILE", type=PropertyType.TEXT,
        state=PropertyState.OK,
        items={"FILE": IndigoItem(name="FILE", value_text="/tmp/missing_capture_001.fits")},
    )

    await adapter._on_property_updated(prop)

    on_image_received.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_update_local_mode_dir() -> None:
    """Test adapter caches local mode directory."""
    adapter = _make_adapter()
    prop = IndigoProperty(
        device="TestCCD", name="CCD_LOCAL_MODE", type=PropertyType.TEXT,
        state=PropertyState.OK,
        items={"DIR": IndigoItem(name="DIR", value_text="/data/images/")},
    )

    await adapter._on_property_updated(prop)

    assert adapter._local_mode_dir == "/data/images/"


# ── Property Delete Callback Tests ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_on_delete_all_properties_resets_cache() -> None:
    """Test adapter resets cache when all device properties are deleted."""
    adapter = _make_adapter()
    adapter._device_connected = True
    adapter._exposure_state = "Busy"
    adapter._gain = 100
    adapter._last_image_path = "/data/image.fits"

    await adapter._on_property_deleted("TestCCD", "")

    assert adapter._device_connected is False
    assert adapter._exposure_state == "IDLE"
    assert adapter._gain is None
    assert adapter._last_image_path is None


@pytest.mark.asyncio
async def test_on_delete_single_property_does_not_reset() -> None:
    """Test adapter does NOT reset full cache for a single property deletion."""
    adapter = _make_adapter()
    adapter._device_connected = True
    adapter._gain = 100

    await adapter._on_property_deleted("TestCCD", "CCD_STREAMING")

    assert adapter._device_connected is True
    assert adapter._gain == 100


# ── Auto-reconnect Scheduling Tests ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_on_update_disconnect_schedules_autoconnect() -> None:
    """Test adapter schedules delayed reconnect when device disconnects."""
    adapter = _make_adapter(auto_connect=True)
    adapter._device_connected = True
    prop = IndigoProperty(
        device="TestCCD", name="CONNECTION", type=PropertyType.SWITCH,
        state=PropertyState.OK,
        items={
            "CONNECTED": IndigoItem(name="CONNECTED", value_switch=False),
            "DISCONNECTED": IndigoItem(name="DISCONNECTED", value_switch=True),
        },
    )

    await adapter._on_property_updated(prop)

    assert adapter._device_connected is False
    assert adapter._autoconnect_task is not None
    # Clean up the task to avoid warnings
    adapter._autoconnect_task.cancel()


@pytest.mark.asyncio
async def test_autoconnect_not_duplicated() -> None:
    """Test _schedule_autoconnect does not create duplicate tasks."""
    adapter = _make_adapter(auto_connect=True)
    # Simulate an already-pending autoconnect task
    fake_task = MagicMock()
    fake_task.done.return_value = False
    adapter._autoconnect_task = fake_task

    adapter._schedule_autoconnect()

    # Should not have been replaced
    assert adapter._autoconnect_task is fake_task


# ── Cooler State Derivation Tests ────────────────────────────────────────────


def test_derive_cooler_state_off() -> None:
    """Test cooler state is OFF when cooler is disabled."""
    adapter = _make_adapter()
    adapter._cooler_on = False
    adapter._derive_cooler_state()
    assert adapter._cooler_state == "OFF"


def test_derive_cooler_state_alarm() -> None:
    """Test cooler state is ALARM when power >= 99%."""
    adapter = _make_adapter()
    adapter._cooler_on = True
    adapter._cooler_power_pct = 99.5
    adapter._derive_cooler_state()
    assert adapter._cooler_state == "ALARM"


def test_derive_cooler_state_stable() -> None:
    """Test cooler state is STABLE when temp is within 1C of target."""
    adapter = _make_adapter()
    adapter._cooler_on = True
    adapter._cooler_power_pct = 45.0
    adapter._ccd_temp_c = -20.2
    adapter._cooler_target_c = -20.0
    adapter._derive_cooler_state()
    assert adapter._cooler_state == "STABLE"


def test_derive_cooler_state_ramping() -> None:
    """Test cooler state is RAMPING when temp is far from target."""
    adapter = _make_adapter()
    adapter._cooler_on = True
    adapter._cooler_power_pct = 80.0
    adapter._ccd_temp_c = 5.0
    adapter._cooler_target_c = -20.0
    adapter._derive_cooler_state()
    assert adapter._cooler_state == "RAMPING"


# ── Cache Reset Tests ────────────────────────────────────────────────────────


def test_reset_cache_clears_all_values() -> None:
    """Test _reset_cache sets all fields to defaults."""
    adapter = _make_adapter()
    adapter._exposure_state = "Busy"
    adapter._exposure_value = 5.0
    adapter._ccd_temp_c = -20.0
    adapter._cooler_on = True
    adapter._cooler_state = "STABLE"
    adapter._bin_x = 2
    adapter._bin_y = 2
    adapter._roi = (100, 100, 1920, 1080)
    adapter._gain = 200
    adapter._offset = 50
    adapter._last_image_path = "/img.fits"
    adapter._local_mode_dir = "/data"

    adapter._reset_cache()

    assert adapter._exposure_state == "IDLE"
    assert adapter._exposure_value == 0.0
    assert adapter._ccd_temp_c == 0.0
    assert adapter._cooler_on is False
    assert adapter._cooler_state == "OFF"
    assert adapter._bin_x == 1
    assert adapter._bin_y == 1
    assert adapter._roi == (0, 0, 0, 0)
    assert adapter._gain is None
    assert adapter._offset is None
    assert adapter._last_image_path is None
    assert adapter._local_mode_dir is None


# ── get_state Snapshot Tests ─────────────────────────────────────────────────


def test_get_state_returns_correct_snapshot() -> None:
    """Test get_state returns a CameraStateSnapshot with all cached values."""
    adapter = _make_adapter()
    adapter._device_connected = True
    adapter._exposure_state = "Ok"
    adapter._exposure_value = 0.0
    adapter._exposure_target = 5.0
    adapter._ccd_temp_c = -20.0
    adapter._cooler_target_c = -20.0
    adapter._cooler_power_pct = 40.0
    adapter._cooler_on = True
    adapter._cooler_state = "STABLE"
    adapter._bin_x = 2
    adapter._bin_y = 2
    adapter._roi = (0, 0, 4656, 3520)
    adapter._gain = 100
    adapter._offset = 10
    adapter._last_image_path = "/data/test.fits"
    adapter._local_mode_dir = "/data"

    state = adapter.get_state()

    assert state.device_name == "TestCCD"
    assert state.connected is True
    assert state.exposure_state == "Ok"
    assert state.exposure_target == pytest.approx(5.0)
    assert state.ccd_temp_c == pytest.approx(-20.0)
    assert state.cooler_target_c == pytest.approx(-20.0)
    assert state.cooler_power_pct == pytest.approx(40.0)
    assert state.cooler_on is True
    assert state.cooler_state == "STABLE"
    assert state.bin_x == 2
    assert state.bin_y == 2
    assert state.roi == (0, 0, 4656, 3520)
    assert state.gain == 100
    assert state.offset == 10
    assert state.last_image_path == "/data/test.fits"
    assert state.local_mode_dir == "/data"


# ── No Polling Verification ─────────────────────────────────────────────────


def test_no_polling_loops_exist() -> None:
    """Verify adapter has no polling loops (no _connection_refresh_loop)."""
    adapter = _make_adapter()
    assert not hasattr(adapter, "_connection_refresh_loop")
    assert not hasattr(adapter, "_cache_updater")
    assert not hasattr(adapter, "_refresh_task")
    assert not hasattr(adapter, "_event_task")
