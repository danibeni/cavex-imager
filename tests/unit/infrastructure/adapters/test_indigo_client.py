"""Unit tests for INDIGO event-driven TCP client."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.infrastructure.adapters.indigo_client import (
    IndigoClient,
    IndigoEvent,
    IndigoItem,
    IndigoProperty,
    PropertyCache,
    PropertyEventType,
    PropertyPerm,
    PropertyState,
    PropertyType,
)


# ── Connection Tests ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_connect_opens_connection_correctly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test connect opens TCP connection and starts read loop."""
    client = IndigoClient()
    reader = AsyncMock()
    writer = MagicMock()
    writer.write = MagicMock()
    writer.drain = AsyncMock()

    async def fake_open_connection(host: str, port: int) -> tuple[AsyncMock, MagicMock]:
        del host, port
        return reader, writer

    created_tasks: list[MagicMock] = []

    def fake_create_task(coro: object) -> MagicMock:
        if hasattr(coro, "close"):
            coro.close()
        task = MagicMock()
        created_tasks.append(task)
        return task

    monkeypatch.setattr(asyncio, "open_connection", fake_open_connection)
    monkeypatch.setattr(asyncio, "create_task", fake_create_task)

    await client.connect("localhost", 7624)

    assert client._reader is reader
    assert client._writer is writer
    assert client.is_connected is True
    assert len(created_tasks) == 1  # One task for read loop


# ── Command Sending Tests ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_number_sends_correct_xml() -> None:
    """Test send_number writes expected INDIGO newNumberVector XML."""
    client = IndigoClient()
    writer = MagicMock()
    writer.drain = AsyncMock()
    client._writer = writer
    client._connected = True

    await client.send_number("CCD Simulator", "CCD_EXPOSURE", {"EXPOSURE": 5.0})

    writer.write.assert_called_once()
    sent_bytes = writer.write.call_args.args[0]
    sent_xml = sent_bytes.decode("utf-8")
    assert 'device="CCD Simulator"' in sent_xml
    assert 'name="CCD_EXPOSURE"' in sent_xml
    assert "newNumberVector" in sent_xml
    assert ">5.0<" in sent_xml


@pytest.mark.asyncio
async def test_send_switch_sends_correct_xml() -> None:
    """Test send_switch writes expected INDIGO newSwitchVector XML."""
    client = IndigoClient()
    writer = MagicMock()
    writer.drain = AsyncMock()
    client._writer = writer
    client._connected = True

    await client.send_switch("CAVEXCam", "CONNECTION", {"CONNECTED": True, "DISCONNECTED": False})

    writer.write.assert_called_once()
    sent = writer.write.call_args.args[0].decode("utf-8")
    assert "newSwitchVector" in sent
    assert 'name="CONNECTION"' in sent
    assert ">On<" in sent
    assert ">Off<" in sent


@pytest.mark.asyncio
async def test_send_text_sends_correct_xml() -> None:
    """Test send_text writes expected INDIGO newTextVector XML."""
    client = IndigoClient()
    writer = MagicMock()
    writer.drain = AsyncMock()
    client._writer = writer
    client._connected = True

    await client.send_text("CAVEXCam", "CCD_LOCAL_MODE", {"DIR": "/data/", "PREFIX": "cavex"})

    writer.write.assert_called_once()
    sent = writer.write.call_args.args[0].decode("utf-8")
    assert "newTextVector" in sent
    assert "/data/" in sent
    assert "cavex" in sent


@pytest.mark.asyncio
async def test_enable_blob_sends_correct_xml() -> None:
    """Test enable_blob writes expected enableBLOB XML."""
    client = IndigoClient()
    writer = MagicMock()
    writer.drain = AsyncMock()
    client._writer = writer
    client._connected = True

    await client.enable_blob("CCD Simulator", "CCD_IMAGE", "URL")

    sent = writer.write.call_args.args[0].decode("utf-8")
    assert 'enableBLOB' in sent
    assert 'device="CCD Simulator"' in sent
    assert 'name="CCD_IMAGE"' in sent
    assert ">URL<" in sent


# ── XML Parsing Tests ────────────────────────────────────────────────────────


def test_parse_element_parses_def_switch_vector() -> None:
    """Test _parse_element correctly classifies defSwitchVector as DEFINE."""
    xml = (
        '<defSwitchVector device="CCD Simulator" name="CONNECTION"'
        ' group="Main" label="Connection" rule="OneOfMany" state="Ok" perm="rw">'
        '<defSwitch name="CONNECTED" label="Connected">Off</defSwitch>'
        '<defSwitch name="DISCONNECTED" label="Disconnected">On</defSwitch>'
        "</defSwitchVector>"
    )

    event = IndigoClient._parse_element(xml)

    assert event is not None
    assert event.event_type == PropertyEventType.DEFINE
    assert event.device == "CCD Simulator"
    assert event.property_name == "CONNECTION"
    assert event.property_type == PropertyType.SWITCH
    assert event.state == PropertyState.OK
    assert event.perm == PropertyPerm.RW
    assert event.rule == "OneOfMany"
    assert event.group == "Main"
    # Items
    assert "CONNECTED" in event.items
    assert event.items["CONNECTED"].value_switch is False
    assert event.items["CONNECTED"].label == "Connected"
    assert "DISCONNECTED" in event.items
    assert event.items["DISCONNECTED"].value_switch is True


def test_parse_element_parses_set_switch_vector() -> None:
    """Test _parse_element correctly classifies setSwitchVector as UPDATE."""
    xml = (
        '<setSwitchVector device="CCD Simulator" name="CONNECTION" state="Ok">'
        '<oneSwitch name="CONNECTED">On</oneSwitch>'
        '<oneSwitch name="DISCONNECTED">Off</oneSwitch>'
        "</setSwitchVector>"
    )

    event = IndigoClient._parse_element(xml)

    assert event is not None
    assert event.event_type == PropertyEventType.UPDATE
    assert event.property_type == PropertyType.SWITCH
    assert event.state == PropertyState.OK
    assert event.items["CONNECTED"].value_switch is True
    assert event.items["DISCONNECTED"].value_switch is False


def test_parse_element_parses_set_number_vector_with_target() -> None:
    """Test _parse_element extracts target attribute from setNumberVector."""
    xml = (
        '<setNumberVector device="CAVEXCam" name="CCD_TEMPERATURE" state="Busy">'
        '<oneNumber name="TEMPERATURE" target="-20">5.3</oneNumber>'
        "</setNumberVector>"
    )

    event = IndigoClient._parse_element(xml)

    assert event is not None
    assert event.event_type == PropertyEventType.UPDATE
    assert event.property_type == PropertyType.NUMBER
    assert event.state == PropertyState.BUSY
    assert event.items["TEMPERATURE"].value_number == pytest.approx(5.3)
    assert event.items["TEMPERATURE"].target_number == pytest.approx(-20.0)


def test_parse_element_parses_def_number_vector_metadata() -> None:
    """Test _parse_element extracts min/max/step/format from defNumberVector."""
    xml = (
        '<defNumberVector device="CCD Simulator" name="CCD_EXPOSURE"'
        ' group="Camera" label="Start exposure" state="Idle" perm="rw">'
        '<defNumber name="EXPOSURE" label="Start exposure"'
        ' min="0" max="10000" step="1" format="%g" target="0">0</defNumber>'
        "</defNumberVector>"
    )

    event = IndigoClient._parse_element(xml)

    assert event is not None
    assert event.event_type == PropertyEventType.DEFINE
    assert event.property_type == PropertyType.NUMBER
    assert event.perm == PropertyPerm.RW
    item = event.items["EXPOSURE"]
    assert item.min_number == pytest.approx(0.0)
    assert item.max_number == pytest.approx(10000.0)
    assert item.step_number == pytest.approx(1.0)
    assert item.format_number == "%g"
    assert item.target_number == pytest.approx(0.0)
    assert item.value_number == pytest.approx(0.0)


def test_parse_element_parses_delete_property() -> None:
    """Test _parse_element handles deleteProperty events."""
    xml = '<deleteProperty device="CCD Simulator" name="CCD_STREAMING"/>'

    event = IndigoClient._parse_element(xml)

    assert event is not None
    assert event.event_type == PropertyEventType.DELETE
    assert event.device == "CCD Simulator"
    assert event.property_name == "CCD_STREAMING"


def test_parse_element_parses_delete_all_device_properties() -> None:
    """Test _parse_element handles deleteProperty without property name."""
    xml = '<deleteProperty device="CCD Simulator"/>'

    event = IndigoClient._parse_element(xml)

    assert event is not None
    assert event.event_type == PropertyEventType.DELETE
    assert event.device == "CCD Simulator"
    assert event.property_name == ""


def test_parse_element_parses_message() -> None:
    """Test _parse_element handles INDIGO message elements."""
    xml = '<message device="CCD Simulator" message="Exposure complete"/>'

    event = IndigoClient._parse_element(xml)

    assert event is not None
    assert event.event_type == PropertyEventType.MESSAGE
    assert event.device == "CCD Simulator"
    assert event.message == "Exposure complete"


def test_parse_element_handles_switch_protocol() -> None:
    """Test _parse_element returns None for switchProtocol (informational)."""
    xml = '<switchProtocol version="2.0"/>'

    event = IndigoClient._parse_element(xml)

    assert event is None


def test_parse_element_parses_set_blob_vector_url() -> None:
    """Test _parse_element extracts BLOB URL from setBLOBVector."""
    xml = (
        '<setBLOBVector device="CCD Simulator" name="CCD_IMAGE" state="Ok">'
        '<oneBLOB name="IMAGE" url="http://localhost:7624/blob/0x123.fits" format=".fits"/>'
        "</setBLOBVector>"
    )

    event = IndigoClient._parse_element(xml)

    assert event is not None
    assert event.event_type == PropertyEventType.UPDATE
    assert event.property_type == PropertyType.BLOB
    assert event.items["IMAGE"].value_blob_url == "http://localhost:7624/blob/0x123.fits"
    assert event.items["IMAGE"].value_blob_format == ".fits"


def test_parse_element_returns_none_for_invalid_xml() -> None:
    """Test _parse_element returns None for unparseable XML."""
    event = IndigoClient._parse_element("<broken xml!!")
    assert event is None


# ── XML Buffer Extraction Tests ──────────────────────────────────────────────


def test_try_extract_element_with_complete_xml() -> None:
    """Test _try_extract_element finds complete element."""
    buf = (
        '<setNumberVector device="CAVEXCam" name="CCD_TEMPERATURE" state="Ok">'
        '<oneNumber name="TEMPERATURE">-10.2</oneNumber>'
        "</setNumberVector>"
        "leftover"
    )

    element, remainder = IndigoClient._try_extract_element(buf)

    assert element is not None
    assert "CCD_TEMPERATURE" in element
    assert remainder == "leftover"


def test_try_extract_element_with_incomplete_xml() -> None:
    """Test _try_extract_element returns None for incomplete XML."""
    buf = '<setNumberVector device="CAVEXCam" name="CCD_TEMPERATURE">'

    element, remainder = IndigoClient._try_extract_element(buf)

    assert element is None
    assert remainder == buf


def test_try_extract_element_with_self_closing_tag() -> None:
    """Test _try_extract_element handles self-closing root tags."""
    buf = '<deleteProperty device="CCD Simulator"/>more'

    element, remainder = IndigoClient._try_extract_element(buf)

    assert element is not None
    assert "deleteProperty" in element
    assert remainder == "more"


def test_try_extract_element_root_with_self_closing_child() -> None:
    """Test extractor does not stop at inner /> (e.g. defBLOB inside defBLOBVector)."""
    buf = (
        '<defBLOBVector device="CCD Imager Simulator" name="CCD_IMAGE"'
        ' group="Image" label="Image data" perm="ro" state="Ok">'
        "<defBLOB name='IMAGE' label='Image data'/>"
        "</defBLOBVector>"
        "<defSwitchVector device='CCD' name='CONNECTION'>"
    )

    element, remainder = IndigoClient._try_extract_element(buf)

    assert element is not None
    assert "defBLOBVector" in element
    assert "defBLOB" in element
    assert element.endswith("</defBLOBVector>")
    assert remainder.strip().startswith("<defSwitchVector")


# ── PropertyCache Tests ──────────────────────────────────────────────────────


def test_cache_handle_define_creates_property() -> None:
    """Test PropertyCache stores a new property on define."""
    cache = PropertyCache()
    event = IndigoEvent(
        event_type=PropertyEventType.DEFINE,
        device="CCD",
        property_name="CONNECTION",
        property_type=PropertyType.SWITCH,
        state=PropertyState.OK,
        perm=PropertyPerm.RW,
        rule="OneOfMany",
        items={
            "CONNECTED": IndigoItem(name="CONNECTED", value_switch=False),
            "DISCONNECTED": IndigoItem(name="DISCONNECTED", value_switch=True),
        },
    )

    prop = cache.handle_define(event)

    assert prop.device == "CCD"
    assert prop.name == "CONNECTION"
    assert prop.type == PropertyType.SWITCH
    assert prop.state == PropertyState.OK
    assert prop.items["CONNECTED"].value_switch is False
    assert cache.get("CCD", "CONNECTION") is prop


def test_cache_handle_update_merges_values() -> None:
    """Test PropertyCache merges set values into existing property."""
    cache = PropertyCache()
    # First define the property
    define_event = IndigoEvent(
        event_type=PropertyEventType.DEFINE,
        device="CCD",
        property_name="CCD_TEMPERATURE",
        property_type=PropertyType.NUMBER,
        state=PropertyState.IDLE,
        items={
            "TEMPERATURE": IndigoItem(
                name="TEMPERATURE", value_number=20.0, target_number=0.0,
                min_number=-50.0, max_number=50.0,
            ),
        },
    )
    cache.handle_define(define_event)

    # Then update it
    update_event = IndigoEvent(
        event_type=PropertyEventType.UPDATE,
        device="CCD",
        property_name="CCD_TEMPERATURE",
        property_type=PropertyType.NUMBER,
        state=PropertyState.BUSY,
        items={
            "TEMPERATURE": IndigoItem(name="TEMPERATURE", value_number=5.3, target_number=-20.0),
        },
    )
    prop = cache.handle_update(update_event)

    assert prop.state == PropertyState.BUSY
    assert prop.items["TEMPERATURE"].value_number == pytest.approx(5.3)
    assert prop.items["TEMPERATURE"].target_number == pytest.approx(-20.0)
    # Definition metadata preserved
    assert prop.items["TEMPERATURE"].min_number == pytest.approx(-50.0)


def test_cache_handle_delete_removes_specific_property() -> None:
    """Test PropertyCache removes a single property on delete."""
    cache = PropertyCache()
    cache.handle_define(IndigoEvent(
        event_type=PropertyEventType.DEFINE,
        device="CCD", property_name="PROP_A", property_type=PropertyType.TEXT,
    ))
    cache.handle_define(IndigoEvent(
        event_type=PropertyEventType.DEFINE,
        device="CCD", property_name="PROP_B", property_type=PropertyType.TEXT,
    ))

    deleted = cache.handle_delete(IndigoEvent(
        event_type=PropertyEventType.DELETE, device="CCD", property_name="PROP_A",
    ))

    assert deleted == ["PROP_A"]
    assert cache.get("CCD", "PROP_A") is None
    assert cache.get("CCD", "PROP_B") is not None


def test_cache_handle_delete_removes_all_device_properties() -> None:
    """Test PropertyCache removes all device properties when name is empty."""
    cache = PropertyCache()
    cache.handle_define(IndigoEvent(
        event_type=PropertyEventType.DEFINE,
        device="CCD", property_name="PROP_A", property_type=PropertyType.TEXT,
    ))
    cache.handle_define(IndigoEvent(
        event_type=PropertyEventType.DEFINE,
        device="CCD", property_name="PROP_B", property_type=PropertyType.TEXT,
    ))

    deleted = cache.handle_delete(IndigoEvent(
        event_type=PropertyEventType.DELETE, device="CCD", property_name="",
    ))

    assert len(deleted) == 2
    assert cache.get("CCD", "PROP_A") is None
    assert cache.get("CCD", "PROP_B") is None


# ── Callback Dispatch Tests ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_fires_define_callbacks() -> None:
    """Test _dispatch_event calls registered define callbacks."""
    client = IndigoClient()
    received: list[IndigoProperty] = []

    async def on_define(prop: IndigoProperty) -> None:
        received.append(prop)

    client.on_define(on_define, device="CCD")

    event = IndigoEvent(
        event_type=PropertyEventType.DEFINE,
        device="CCD",
        property_name="CONNECTION",
        property_type=PropertyType.SWITCH,
        state=PropertyState.OK,
        items={"CONNECTED": IndigoItem(name="CONNECTED", value_switch=True)},
        raw_tag="defSwitchVector",
    )

    await client._dispatch_event(event)

    assert len(received) == 1
    assert received[0].name == "CONNECTION"
    assert received[0].items["CONNECTED"].value_switch is True


@pytest.mark.asyncio
async def test_dispatch_fires_update_callbacks() -> None:
    """Test _dispatch_event calls registered update callbacks."""
    client = IndigoClient()
    received: list[IndigoProperty] = []

    async def on_update(prop: IndigoProperty) -> None:
        received.append(prop)

    client.on_update(on_update, device="CCD", property_name="CCD_TEMPERATURE")

    event = IndigoEvent(
        event_type=PropertyEventType.UPDATE,
        device="CCD",
        property_name="CCD_TEMPERATURE",
        property_type=PropertyType.NUMBER,
        state=PropertyState.BUSY,
        items={"TEMPERATURE": IndigoItem(name="TEMPERATURE", value_number=5.3, target_number=-20.0)},
        raw_tag="setNumberVector",
    )

    await client._dispatch_event(event)

    assert len(received) == 1
    assert received[0].state == PropertyState.BUSY


@pytest.mark.asyncio
async def test_dispatch_fires_delete_callbacks() -> None:
    """Test _dispatch_event calls registered delete callbacks."""
    client = IndigoClient()
    # Pre-populate cache so delete has something to remove
    client._property_cache.handle_define(IndigoEvent(
        event_type=PropertyEventType.DEFINE,
        device="CCD", property_name="CCD_STREAMING", property_type=PropertyType.SWITCH,
    ))

    deleted_props: list[tuple[str, str]] = []

    async def on_delete(device: str, prop_name: str) -> None:
        deleted_props.append((device, prop_name))

    client.on_delete(on_delete, device="CCD")

    event = IndigoEvent(
        event_type=PropertyEventType.DELETE,
        device="CCD",
        property_name="CCD_STREAMING",
        raw_tag="deleteProperty",
    )

    await client._dispatch_event(event)

    assert deleted_props == [("CCD", "CCD_STREAMING")]


@pytest.mark.asyncio
async def test_dispatch_filters_by_device() -> None:
    """Test callbacks are NOT fired when device filter doesn't match."""
    client = IndigoClient()
    received: list[IndigoProperty] = []

    async def on_update(prop: IndigoProperty) -> None:
        received.append(prop)

    client.on_update(on_update, device="OTHER_DEVICE")

    event = IndigoEvent(
        event_type=PropertyEventType.UPDATE,
        device="CCD",
        property_name="CCD_TEMPERATURE",
        property_type=PropertyType.NUMBER,
        state=PropertyState.OK,
        items={"TEMPERATURE": IndigoItem(name="TEMPERATURE", value_number=-20.0)},
        raw_tag="setNumberVector",
    )

    await client._dispatch_event(event)

    assert len(received) == 0


@pytest.mark.asyncio
async def test_dispatch_without_filter_receives_all() -> None:
    """Test callbacks without filters receive events from all devices."""
    client = IndigoClient()
    received: list[IndigoProperty] = []

    async def on_update(prop: IndigoProperty) -> None:
        received.append(prop)

    client.on_update(on_update)  # No filters

    for device in ["CCD_A", "CCD_B", "Focuser"]:
        event = IndigoEvent(
            event_type=PropertyEventType.UPDATE,
            device=device,
            property_name="CONNECTION",
            property_type=PropertyType.SWITCH,
            state=PropertyState.OK,
            items={"CONNECTED": IndigoItem(name="CONNECTED", value_switch=True)},
            raw_tag="setSwitchVector",
        )
        await client._dispatch_event(event)

    assert len(received) == 3
