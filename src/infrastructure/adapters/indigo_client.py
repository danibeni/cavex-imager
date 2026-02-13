"""Async TCP client for INDIGO protocol with streaming XML and reconnection."""

from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from typing import Any, AsyncIterator

import structlog

logger = structlog.get_logger(__name__)

# INDIGO vector tag to child element tag mapping
_VECTOR_CHILD_MAP: dict[str, str] = {
    "newNumberVector": "oneNumber",
    "newSwitchVector": "oneSwitch",
    "newTextVector": "oneText",
}


class IndigoClient:
    """TCP client wrapper for INDIGO XML messaging with reconnection."""

    def __init__(self) -> None:
        """Initialize client state."""
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._read_task: asyncio.Task[None] | None = None
        self._host: str = ""
        self._port: int = 0
        self._connected = False
        self._reconnect_enabled = True
        self._reconnect_task: asyncio.Task[None] | None = None
        self._xml_buffer = ""

    @property
    def is_connected(self) -> bool:
        """Return True if TCP connection is alive."""
        return self._connected

    async def connect(self, host: str, port: int) -> None:
        """Open TCP connection and start read loop.

        Args:
            host: INDIGO host.
            port: INDIGO port.
        """
        self._host = host
        self._port = port
        await self._establish_connection()

    async def _establish_connection(self) -> None:
        """Open the TCP socket and start background reader."""
        self._reader, self._writer = await asyncio.open_connection(self._host, self._port)
        self._connected = True
        self._xml_buffer = ""
        self._read_task = asyncio.create_task(self._read_loop())

        # Request all properties from INDIGO server
        await self._send_raw('<getProperties version="2.0" />')
        logger.info("indigo_connected", host=self._host, port=self._port)

    async def disconnect(self) -> None:
        """Close connection and stop background tasks."""
        self._reconnect_enabled = False

        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
            self._reconnect_task = None

        if self._read_task is not None:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
            self._read_task = None

        if self._writer is not None:
            self._writer.close()
            await self._writer.wait_closed()
            self._writer = None

        self._reader = None
        self._connected = False
        logger.info("indigo_disconnected")

    # ── Read loop with streaming XML ────────────────────────────────

    async def _read_loop(self) -> None:
        """Read TCP data and parse INDIGO XML messages incrementally."""
        if self._reader is None:
            return

        try:
            while True:
                data = await self._reader.read(65536)
                if not data:
                    break
                self._xml_buffer += data.decode("utf-8", errors="replace")
                await self._process_buffer()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("indigo_read_error", error=str(exc))
        finally:
            self._connected = False
            logger.info("indigo_connection_lost")
            if self._reconnect_enabled:
                self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _process_buffer(self) -> None:
        """Extract complete XML elements from the buffer."""
        # INDIGO messages are top-level XML elements; find closing tags
        while True:
            element, remainder = self._try_extract_element(self._xml_buffer)
            if element is None:
                break
            self._xml_buffer = remainder
            event = self._parse_element(element)
            if event:
                await self._event_queue.put(event)

    @staticmethod
    def _try_extract_element(buf: str) -> tuple[str | None, str]:
        """Try to extract one complete XML element from buffer.

        Returns:
            Tuple of (element_string or None, remaining_buffer).
        """
        # Skip leading whitespace
        stripped = buf.lstrip()
        if not stripped or not stripped.startswith("<"):
            return None, buf

        # Find tag name
        tag_end = stripped.find(" ")
        close_bracket = stripped.find(">")
        if close_bracket == -1:
            return None, buf

        tag_name_end = min(tag_end, close_bracket) if tag_end != -1 else close_bracket
        tag_name = stripped[1:tag_name_end]

        # Self-closing tag: check before closing tag
        self_close = stripped.find("/>")
        closing_tag = f"</{tag_name}>"
        close_idx = stripped.find(closing_tag)

        if self_close != -1 and (close_idx == -1 or self_close < close_idx):
            end = self_close + 2
            return stripped[:end], stripped[end:]

        # Find closing tag
        if close_idx == -1:
            return None, buf

        end = close_idx + len(closing_tag)
        return stripped[:end], stripped[end:]

    @staticmethod
    def _parse_element(xml_str: str) -> dict[str, Any] | None:
        """Parse an INDIGO XML element into a dictionary.

        Args:
            xml_str: Complete XML element string.

        Returns:
            Parsed event dictionary, or None on parse error.
        """
        try:
            root = ET.fromstring(xml_str)
        except ET.ParseError:
            logger.debug("indigo_xml_parse_error", xml=xml_str[:200])
            return None

        child_values: dict[str, str] = {}
        for child in root:
            name = child.attrib.get("name", child.tag)
            child_values[name] = (child.text or "").strip()

        event: dict[str, Any] = {
            "tag": root.tag,
            "attributes": dict(root.attrib),
            "values": child_values,
        }
        if "device" in root.attrib:
            event["device"] = root.attrib["device"]
        if "name" in root.attrib:
            event["property"] = root.attrib["name"]
        if "state" in root.attrib:
            event["state"] = root.attrib["state"]
        return event

    # ── Reconnection with exponential backoff ───────────────────────

    async def _reconnect_loop(self) -> None:
        """Reconnect with exponential backoff: 1s, 2s, 5s, 10s, 30s max."""
        backoff_sequence = [1, 2, 5, 10, 30]
        attempt = 0

        while self._reconnect_enabled:
            delay = backoff_sequence[min(attempt, len(backoff_sequence) - 1)]
            logger.info("indigo_reconnect_attempt", attempt=attempt + 1, delay_s=delay)
            await asyncio.sleep(delay)

            try:
                await self._establish_connection()
                logger.info("indigo_reconnect_success", attempt=attempt + 1)
                return
            except Exception as exc:
                logger.warning("indigo_reconnect_failed", attempt=attempt + 1, error=str(exc))
                attempt += 1

    # ── Command sending ─────────────────────────────────────────────

    async def _send_raw(self, xml: str) -> None:
        """Send raw XML string over TCP.

        Args:
            xml: XML command string.

        Raises:
            RuntimeError: If not connected.
        """
        if self._writer is None:
            raise RuntimeError("Not connected to INDIGO server.")
        self._writer.write(xml.encode("utf-8"))
        await self._writer.drain()

    async def send_number(self, device: str, prop: str, items: dict[str, float]) -> None:
        """Send a newNumberVector command.

        Args:
            device: INDIGO device name.
            prop: Property name.
            items: Mapping of element name to numeric value.
        """
        children = "".join(
            f'<oneNumber name="{k}">{v}</oneNumber>' for k, v in items.items()
        )
        xml = f'<newNumberVector device="{device}" name="{prop}">{children}</newNumberVector>'
        await self._send_raw(xml)
        logger.debug("indigo_send_number", device=device, property=prop, items=items)

    async def send_switch(self, device: str, prop: str, items: dict[str, bool]) -> None:
        """Send a newSwitchVector command.

        Args:
            device: INDIGO device name.
            prop: Property name.
            items: Mapping of element name to on/off state.
        """
        children = "".join(
            f'<oneSwitch name="{k}">{"On" if v else "Off"}</oneSwitch>'
            for k, v in items.items()
        )
        xml = f'<newSwitchVector device="{device}" name="{prop}">{children}</newSwitchVector>'
        await self._send_raw(xml)
        logger.debug("indigo_send_switch", device=device, property=prop, items=items)

    async def send_text(self, device: str, prop: str, items: dict[str, str]) -> None:
        """Send a newTextVector command.

        Args:
            device: INDIGO device name.
            prop: Property name.
            items: Mapping of element name to text value.
        """
        children = "".join(
            f'<oneText name="{k}">{v}</oneText>' for k, v in items.items()
        )
        xml = f'<newTextVector device="{device}" name="{prop}">{children}</newTextVector>'
        await self._send_raw(xml)
        logger.debug("indigo_send_text", device=device, property=prop, items=items)

    # ── Event stream ────────────────────────────────────────────────

    async def event_stream(self) -> AsyncIterator[dict[str, Any]]:
        """Yield parsed INDIGO events from queue."""
        while True:
            event = await self._event_queue.get()
            yield event
