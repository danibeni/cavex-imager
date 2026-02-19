"""Event-driven async TCP client for INDIGO protocol.

Implements the INDIGO push-based communication model: the server pushes
property definitions (defXXXVector), updates (setXXXVector) and deletions
(deleteProperty) over a persistent TCP connection. The client dispatches
these events to registered callbacks after updating a central property cache.

Mirrors the C reference client architecture (indigo_bus.c).
"""

from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable

import structlog

logger = structlog.get_logger(__name__)


# ── INDIGO Data Models ──────────────────────────────────────────────────────


class PropertyEventType(Enum):
    """Type of INDIGO event derived from the XML tag."""

    DEFINE = "define"    # defXXXVector — property announced
    UPDATE = "update"    # setXXXVector — property value changed
    DELETE = "delete"    # deleteProperty — property removed
    MESSAGE = "message"  # message — informational text


class PropertyType(Enum):
    """INDIGO property value types."""

    TEXT = "text"
    NUMBER = "number"
    SWITCH = "switch"
    LIGHT = "light"
    BLOB = "blob"


class PropertyState(Enum):
    """INDIGO property states."""

    IDLE = "Idle"
    OK = "Ok"
    BUSY = "Busy"
    ALERT = "Alert"


class PropertyPerm(Enum):
    """INDIGO property permission."""

    RO = "ro"
    RW = "rw"
    WO = "wo"


@dataclass
class IndigoItem:
    """Single item within an INDIGO property (e.g. oneNumber, oneSwitch)."""

    name: str
    label: str = ""
    # Text
    value_text: str = ""
    # Number
    value_number: float = 0.0
    target_number: float = 0.0
    min_number: float = 0.0
    max_number: float = 0.0
    step_number: float = 0.0
    format_number: str = ""
    # Switch
    value_switch: bool = False
    # Light
    value_light: PropertyState = PropertyState.IDLE
    # BLOB
    value_blob_url: str = ""
    value_blob_format: str = ""


@dataclass
class IndigoProperty:
    """Full representation of an INDIGO property with all its items."""

    device: str
    name: str
    type: PropertyType
    state: PropertyState = PropertyState.IDLE
    perm: PropertyPerm = PropertyPerm.RO
    group: str = ""
    label: str = ""
    rule: str = ""  # Switch rule: OneOfMany, AtMostOne, AnyOfMany
    items: dict[str, IndigoItem] = field(default_factory=dict)
    message: str = ""


@dataclass
class IndigoEvent:
    """Parsed event emitted by the XML stream parser."""

    event_type: PropertyEventType
    device: str
    property_name: str = ""
    property_type: PropertyType | None = None
    state: PropertyState | None = None
    perm: PropertyPerm | None = None
    group: str = ""
    label: str = ""
    rule: str = ""
    items: dict[str, IndigoItem] = field(default_factory=dict)
    message: str = ""
    raw_tag: str = ""


# ── XML Tag Classification ──────────────────────────────────────────────────

_TAG_TO_PROPERTY_TYPE: dict[str, PropertyType] = {
    "defTextVector": PropertyType.TEXT,
    "defNumberVector": PropertyType.NUMBER,
    "defSwitchVector": PropertyType.SWITCH,
    "defLightVector": PropertyType.LIGHT,
    "defBLOBVector": PropertyType.BLOB,
    "setTextVector": PropertyType.TEXT,
    "setNumberVector": PropertyType.NUMBER,
    "setSwitchVector": PropertyType.SWITCH,
    "setLightVector": PropertyType.LIGHT,
    "setBLOBVector": PropertyType.BLOB,
}

_TAG_TO_EVENT_TYPE: dict[str, PropertyEventType] = {
    "defTextVector": PropertyEventType.DEFINE,
    "defNumberVector": PropertyEventType.DEFINE,
    "defSwitchVector": PropertyEventType.DEFINE,
    "defLightVector": PropertyEventType.DEFINE,
    "defBLOBVector": PropertyEventType.DEFINE,
    "setTextVector": PropertyEventType.UPDATE,
    "setNumberVector": PropertyEventType.UPDATE,
    "setSwitchVector": PropertyEventType.UPDATE,
    "setLightVector": PropertyEventType.UPDATE,
    "setBLOBVector": PropertyEventType.UPDATE,
    "deleteProperty": PropertyEventType.DELETE,
    "message": PropertyEventType.MESSAGE,
}


# ── Property Cache ──────────────────────────────────────────────────────────


class PropertyCache:
    """Central cache of all INDIGO properties, keyed by (device, property_name).

    Mirrors the property cache in the C reference implementation.
    - handle_define: creates/replaces a property (from defXXXVector)
    - handle_update: merges new values into existing property (from setXXXVector)
    - handle_delete: removes properties (from deleteProperty)
    """

    def __init__(self) -> None:
        self._properties: dict[tuple[str, str], IndigoProperty] = {}

    def handle_define(self, event: IndigoEvent) -> IndigoProperty:
        """Process defXXXVector: create or replace property with full metadata."""
        prop = IndigoProperty(
            device=event.device,
            name=event.property_name,
            type=event.property_type or PropertyType.TEXT,
            state=event.state or PropertyState.IDLE,
            perm=event.perm or PropertyPerm.RO,
            group=event.group,
            label=event.label,
            rule=event.rule,
            items=dict(event.items),
            message=event.message,
        )
        self._properties[(event.device, event.property_name)] = prop
        return prop

    def handle_update(self, event: IndigoEvent) -> IndigoProperty:
        """Process setXXXVector: merge updated item values into cached property."""
        key = (event.device, event.property_name)
        prop = self._properties.get(key)
        if prop is None:
            # Update without prior definition — create minimal entry
            prop = IndigoProperty(
                device=event.device,
                name=event.property_name,
                type=event.property_type or PropertyType.TEXT,
            )
            self._properties[key] = prop

        if event.state is not None:
            prop.state = event.state
        if event.message:
            prop.message = event.message

        # Merge only the items present in this update
        for item_name, new_item in event.items.items():
            existing = prop.items.get(item_name)
            if existing is not None:
                if prop.type == PropertyType.NUMBER:
                    existing.value_number = new_item.value_number
                    if new_item.target_number != 0.0 or existing.target_number == 0.0:
                        existing.target_number = new_item.target_number
                elif prop.type == PropertyType.SWITCH:
                    existing.value_switch = new_item.value_switch
                elif prop.type == PropertyType.TEXT:
                    existing.value_text = new_item.value_text
                elif prop.type == PropertyType.LIGHT:
                    existing.value_light = new_item.value_light
                elif prop.type == PropertyType.BLOB:
                    existing.value_blob_url = new_item.value_blob_url
                    existing.value_blob_format = new_item.value_blob_format
            else:
                prop.items[item_name] = new_item

        return prop

    def handle_delete(self, event: IndigoEvent) -> list[str]:
        """Process deleteProperty: remove properties, return deleted names."""
        deleted: list[str] = []
        if event.property_name:
            key = (event.device, event.property_name)
            if key in self._properties:
                del self._properties[key]
                deleted.append(event.property_name)
        else:
            # Empty name = delete ALL properties for device
            keys = [k for k in self._properties if k[0] == event.device]
            for k in keys:
                deleted.append(k[1])
                del self._properties[k]
        return deleted

    def get(self, device: str, name: str) -> IndigoProperty | None:
        """Get a cached property by device and name."""
        return self._properties.get((device, name))

    def get_device_properties(self, device: str) -> list[IndigoProperty]:
        """Get all cached properties for a device."""
        return [p for k, p in self._properties.items() if k[0] == device]

    def clear(self) -> None:
        """Clear all cached properties (used on reconnection)."""
        self._properties.clear()


# ── Callback Type Aliases ───────────────────────────────────────────────────

PropertyCallback = Callable[[IndigoProperty], Awaitable[None]]
DeleteCallback = Callable[[str, str], Awaitable[None]]  # (device, property_name)


# ── INDIGO Client ───────────────────────────────────────────────────────────


class IndigoClient:
    """Event-driven async TCP client for INDIGO XML protocol.

    Usage:
        client = IndigoClient()
        client.on_define(my_handler)     # register for property definitions
        client.on_update(my_handler)     # register for property updates
        client.on_delete(my_del_handler) # register for property deletions
        await client.connect("localhost", 7624)
        # callbacks fire automatically as events arrive — no polling needed
    """

    def __init__(self) -> None:
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._read_task: asyncio.Task[None] | None = None
        self._host: str = ""
        self._port: int = 0
        self._connected = False
        self._reconnect_enabled = True
        self._reconnect_task: asyncio.Task[None] | None = None
        self._xml_buffer = ""

        # Event-driven core
        self._property_cache = PropertyCache()
        self._define_callbacks: list[tuple[str | None, str | None, PropertyCallback]] = []
        self._update_callbacks: list[tuple[str | None, str | None, PropertyCallback]] = []
        self._delete_callbacks: list[tuple[str | None, str | None, DeleteCallback]] = []

    # ── Properties ───────────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        """True if TCP connection is alive."""
        return self._connected

    @property
    def property_cache(self) -> PropertyCache:
        """Access the central property cache."""
        return self._property_cache

    # ── Callback Registration ────────────────────────────────────────

    def on_define(
        self,
        callback: PropertyCallback,
        device: str | None = None,
        property_name: str | None = None,
    ) -> None:
        """Register callback for property definition events (defXXXVector).

        Args:
            callback: Async function receiving an IndigoProperty.
            device: Filter by device name (None = all devices).
            property_name: Filter by property name (None = all properties).
        """
        self._define_callbacks.append((device, property_name, callback))

    def on_update(
        self,
        callback: PropertyCallback,
        device: str | None = None,
        property_name: str | None = None,
    ) -> None:
        """Register callback for property update events (setXXXVector).

        Args:
            callback: Async function receiving an IndigoProperty.
            device: Filter by device name (None = all devices).
            property_name: Filter by property name (None = all properties).
        """
        self._update_callbacks.append((device, property_name, callback))

    def on_delete(
        self,
        callback: DeleteCallback,
        device: str | None = None,
        property_name: str | None = None,
    ) -> None:
        """Register callback for property deletion events (deleteProperty).

        Args:
            callback: Async function receiving (device, property_name).
            device: Filter by device name (None = all devices).
            property_name: Filter by property name (None = all properties).
        """
        self._delete_callbacks.append((device, property_name, callback))

    # ── Connection Lifecycle ─────────────────────────────────────────

    async def connect(self, host: str, port: int) -> None:
        """Open TCP connection and start event read loop.

        Args:
            host: INDIGO server host.
            port: INDIGO server port (default 7624).
        """
        self._host = host
        self._port = port
        try:
            await self._establish_connection()
        except Exception:
            if self._reconnect_enabled and (
                self._reconnect_task is None or self._reconnect_task.done()
            ):
                self._reconnect_task = asyncio.create_task(self._reconnect_loop())
            raise

    async def _establish_connection(self) -> None:
        """Open TCP socket, reset state, start reader, send INDIGO handshake."""
        self._reader, self._writer = await asyncio.open_connection(self._host, self._port)
        self._connected = True
        self._xml_buffer = ""
        self._property_cache.clear()
        self._read_task = asyncio.create_task(self._read_loop())

        # INDIGO v2.0 handshake — server will respond with defXXXVector flood
        await self._send_raw('<getProperties version="2.0" />')
        logger.info("indigo_connected", host=self._host, port=self._port)

    async def disconnect(self) -> None:
        """Close connection and stop all background tasks."""
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

    # ── TCP Read Loop ────────────────────────────────────────────────

    async def _read_loop(self) -> None:
        """Read TCP data continuously and process INDIGO XML messages."""
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
                logger.warning(
                    "indigo_server_unavailable",
                    host=self._host,
                    port=self._port,
                    reason="connection_lost",
                )
                self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _process_buffer(self) -> None:
        """Extract complete XML elements from buffer and dispatch as events."""
        while True:
            element_str, remainder = self._try_extract_element(self._xml_buffer)
            if element_str is None:
                break
            self._xml_buffer = remainder
            event = self._parse_element(element_str)
            if event is not None:
                await self._dispatch_event(event)

    # ── XML Element Extraction ───────────────────────────────────────

    @staticmethod
    def _try_extract_element(buf: str) -> tuple[str | None, str]:
        """Extract one complete top-level XML element from the buffer.

        Only treats the root as self-closing when the root's opening tag ends
        with />; inner self-closing children (e.g. <defBLOB .../>) must not
        be mistaken for the end of the root element.

        Returns:
            (element_string, remaining_buffer) or (None, original_buffer).
        """
        stripped = buf.lstrip()
        if not stripped or not stripped.startswith("<"):
            return None, buf

        # Find tag name and end of root opening tag (first '>' after '<')
        tag_end = stripped.find(" ")
        first_gt = stripped.find(">")
        if first_gt == -1:
            return None, buf

        tag_name_end = min(tag_end, first_gt) if tag_end != -1 else first_gt
        tag_name = stripped[1:tag_name_end]

        # Root self-closing: opening tag ends with /> (e.g. <deleteProperty .../>)
        if stripped[first_gt - 1] == "/":
            return stripped[: first_gt + 1], stripped[first_gt + 1 :]

        # Root has children: find the matching </tag_name>
        closing_tag = f"</{tag_name}>"
        close_idx = stripped.find(closing_tag, first_gt)
        if close_idx == -1:
            return None, buf

        end = close_idx + len(closing_tag)
        return stripped[:end], stripped[end:]

    # ── XML Parsing ──────────────────────────────────────────────────

    @staticmethod
    def _parse_element(xml_str: str) -> IndigoEvent | None:
        """Parse an INDIGO XML element into a typed IndigoEvent.

        Classifies the XML tag into DEFINE/UPDATE/DELETE/MESSAGE and extracts
        all attributes and child items including the target attribute for numbers.

        Args:
            xml_str: Complete XML element string.

        Returns:
            IndigoEvent or None if parsing fails or tag is not relevant.
        """
        try:
            root = ET.fromstring(xml_str)
        except ET.ParseError:
            logger.debug("indigo_xml_parse_error", xml=xml_str[:200])
            return None

        tag = root.tag

        # switchProtocol is informational — log and skip
        if tag == "switchProtocol":
            logger.debug("indigo_switch_protocol", version=root.attrib.get("version"))
            return None

        # Only process known event types
        event_type = _TAG_TO_EVENT_TYPE.get(tag)
        if event_type is None:
            return None

        device = root.attrib.get("device", "")
        prop_name = root.attrib.get("name", "")
        prop_type = _TAG_TO_PROPERTY_TYPE.get(tag)

        # Parse state enum
        state: PropertyState | None = None
        state_str = root.attrib.get("state")
        if state_str:
            try:
                state = PropertyState(state_str)
            except ValueError:
                pass

        # Parse perm enum
        perm: PropertyPerm | None = None
        perm_str = root.attrib.get("perm")
        if perm_str:
            try:
                perm = PropertyPerm(perm_str)
            except ValueError:
                pass

        # MESSAGE — no child items
        if event_type == PropertyEventType.MESSAGE:
            return IndigoEvent(
                event_type=event_type,
                device=device,
                message=root.attrib.get("message", ""),
                raw_tag=tag,
            )

        # DELETE — no child items
        if event_type == PropertyEventType.DELETE:
            return IndigoEvent(
                event_type=event_type,
                device=device,
                property_name=prop_name,
                raw_tag=tag,
            )

        # DEFINE / UPDATE — parse child items
        items = _parse_items(root, prop_type, event_type)

        return IndigoEvent(
            event_type=event_type,
            device=device,
            property_name=prop_name,
            property_type=prop_type,
            state=state,
            perm=perm,
            group=root.attrib.get("group", ""),
            label=root.attrib.get("label", ""),
            rule=root.attrib.get("rule", ""),
            items=items,
            message=root.attrib.get("message", ""),
            raw_tag=tag,
        )

    # ── Event Dispatch ───────────────────────────────────────────────

    async def _dispatch_event(self, event: IndigoEvent) -> None:
        """Update property cache and invoke matching callbacks."""

        if event.event_type == PropertyEventType.DEFINE:
            prop = self._property_cache.handle_define(event)
            for dev_f, prop_f, cb in self._define_callbacks:
                if _matches_filter(event.device, event.property_name, dev_f, prop_f):
                    await cb(prop)

        elif event.event_type == PropertyEventType.UPDATE:
            prop = self._property_cache.handle_update(event)
            for dev_f, prop_f, cb in self._update_callbacks:
                if _matches_filter(event.device, event.property_name, dev_f, prop_f):
                    await cb(prop)

        elif event.event_type == PropertyEventType.DELETE:
            deleted = self._property_cache.handle_delete(event)
            for name in deleted:
                for dev_f, prop_f, cb in self._delete_callbacks:
                    if _matches_filter(event.device, name, dev_f, prop_f):
                        await cb(event.device, name)

        elif event.event_type == PropertyEventType.MESSAGE:
            logger.info("indigo_message", device=event.device, message=event.message)

    # ── Reconnection ─────────────────────────────────────────────────

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

    # ── Command Sending ──────────────────────────────────────────────

    async def _send_raw(self, xml: str) -> None:
        """Send raw XML string over TCP.

        Raises:
            RuntimeError: If not connected.
        """
        if self._writer is None:
            raise RuntimeError("Not connected to INDIGO server.")
        self._writer.write(xml.encode("utf-8"))
        await self._writer.drain()

    async def request_property(self, device: str, name: str) -> None:
        """Request a specific property value from INDIGO server.

        Args:
            device: INDIGO device name.
            name: Property name (e.g. 'CONNECTION').
        """
        xml = f'<getProperties version="2.0" device="{device}" name="{name}" />'
        await self._send_raw(xml)
        logger.debug("indigo_request_property", device=device, name=name)

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

    async def enable_blob(self, device: str, name: str, mode: str = "URL") -> None:
        """Enable BLOB transfer for a property.

        Args:
            device: Device name.
            name: Property name (e.g. 'CCD_IMAGE').
            mode: 'URL' (recommended for INDIGO v2.0), 'Also', or 'Never'.
        """
        xml = f'<enableBLOB device="{device}" name="{name}">{mode}</enableBLOB>'
        await self._send_raw(xml)
        logger.debug("indigo_enable_blob", device=device, name=name, mode=mode)


# ── Module-level helpers (keep IndigoClient class clean) ────────────────────


def _matches_filter(
    device: str,
    prop_name: str,
    dev_filter: str | None,
    prop_filter: str | None,
) -> bool:
    """Check if a device/property pair matches optional callback filters."""
    if dev_filter is not None and dev_filter != device:
        return False
    if prop_filter is not None and prop_filter != prop_name:
        return False
    return True


def _parse_items(
    root: ET.Element,
    prop_type: PropertyType | None,
    event_type: PropertyEventType,
) -> dict[str, IndigoItem]:
    """Parse child elements of a defXXXVector or setXXXVector into IndigoItems."""
    items: dict[str, IndigoItem] = {}

    for child in root:
        item_name = child.attrib.get("name", "")
        if not item_name:
            continue

        text = (child.text or "").strip()
        item = IndigoItem(name=item_name, label=child.attrib.get("label", ""))

        if prop_type == PropertyType.NUMBER:
            item.value_number = _safe_float(text)
            # target attribute — critical for INDIGO v2.0 (requested vs current value)
            item.target_number = _safe_float(child.attrib.get("target", ""))
            # Definition metadata (only in defNumberVector)
            if event_type == PropertyEventType.DEFINE:
                item.min_number = _safe_float(child.attrib.get("min", ""))
                item.max_number = _safe_float(child.attrib.get("max", ""))
                item.step_number = _safe_float(child.attrib.get("step", ""))
                item.format_number = child.attrib.get("format", "")

        elif prop_type == PropertyType.SWITCH:
            item.value_switch = text == "On"

        elif prop_type == PropertyType.TEXT:
            item.value_text = text

        elif prop_type == PropertyType.LIGHT:
            try:
                item.value_light = PropertyState(text)
            except ValueError:
                item.value_light = PropertyState.IDLE

        elif prop_type == PropertyType.BLOB:
            item.value_blob_url = child.attrib.get("url", "")
            item.value_blob_format = child.attrib.get("format", "")

        items[item_name] = item

    return items


def _safe_float(value: str) -> float:
    """Convert string to float, returning 0.0 on failure."""
    if not value:
        return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


