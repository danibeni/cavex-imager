"""Unit tests for INDIGO TCP client."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.infrastructure.adapters.indigo_client import IndigoClient


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
    # One task for read loop
    assert len(created_tasks) == 1


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


def test_parse_element_parses_def_vector() -> None:
    """Test _parse_element parses defTextVector correctly."""
    xml = (
        '<defTextVector device="CCD Simulator" name="CONNECTION" state="Ok">'
        '<defText name="STATE">OK</defText>'
        "</defTextVector>"
    )

    event = IndigoClient._parse_element(xml)

    assert event is not None
    assert event["tag"] == "defTextVector"
    assert event["device"] == "CCD Simulator"
    assert event["property"] == "CONNECTION"
    assert event["state"] == "Ok"
    assert event["values"]["STATE"] == "OK"


def test_parse_element_parses_set_number_vector() -> None:
    """Test _parse_element parses setNumberVector with state."""
    xml = (
        '<setNumberVector device="CAVEXCam" name="CCD_TEMPERATURE" state="Ok">'
        '<oneNumber name="TEMPERATURE">-10.2</oneNumber>'
        "</setNumberVector>"
    )

    event = IndigoClient._parse_element(xml)

    assert event is not None
    assert event["tag"] == "setNumberVector"
    assert event["values"]["TEMPERATURE"] == "-10.2"


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
    """Test _try_extract_element handles self-closing tags."""
    buf = '<getProperties version="2.0" />more'

    element, remainder = IndigoClient._try_extract_element(buf)

    assert element is not None
    assert "getProperties" in element
    assert remainder == "more"
