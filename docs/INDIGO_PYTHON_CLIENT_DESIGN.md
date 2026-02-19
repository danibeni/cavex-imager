# INDIGO Event-Driven Python Client — Design Document

**Version:** 1.0  
**Date:** 2026-02-14  
**Purpose:** Provide an AI agent with a detailed understanding of INDIGO's asynchronous communication model and precise guidelines for implementing an event-driven Python client that replaces the current polling-based approach.

---

## Table of Contents

1. [INDIGO Architecture Overview](#1-indigo-architecture-overview)
2. [The INDIGO Bus and Callback Model](#2-the-indigo-bus-and-callback-model)
3. [XML Protocol — Message Taxonomy](#3-xml-protocol--message-taxonomy)
4. [Complete TCP Session Lifecycle](#4-complete-tcp-session-lifecycle)
5. [Property Model in Depth](#5-property-model-in-depth)
6. [Why the Current Python Client Fails](#6-why-the-current-python-client-fails)
7. [Target Architecture for the Python Client](#7-target-architecture-for-the-python-client)
8. [IndigoClient — Detailed Specification](#8-indigoclient--detailed-specification)
9. [IndigoCameraAdapter — Detailed Specification](#9-indigocameraadapter--detailed-specification)
10. [XML Parsing Rules and Edge Cases](#10-xml-parsing-rules-and-edge-cases)
11. [Implementation Checklist](#11-implementation-checklist)

---

## 1. INDIGO Architecture Overview

INDIGO is an asynchronous platform for communication between software entities over a **software bus**. Entities are either **devices** (hardware drivers), **clients** (user interfaces / automation), or **agents** (hybrid device+client).

### Core Principle: Push, Not Poll

The fundamental design principle of INDIGO is **push-based, event-driven communication**. Devices publish property changes to the bus, and the bus broadcasts them to all connected clients. A well-designed INDIGO client **NEVER polls**. It reacts to incoming events.

Quote from INDIGO official documentation:
> *"We encourage developers to design asynchronous clients abstracted from the connection status."*

### Communication Model

```
CLIENT                          SERVER (device/driver)
  |                                  |
  |--- getProperties --------------->|   (1) Request property definitions
  |                                  |
  |<--- defTextVector ---------------|   (2) Property definitions (initial state)
  |<--- defNumberVector -------------|
  |<--- defSwitchVector -------------|
  |<--- defBLOBVector ---------------|
  |       ...                        |
  |                                  |
  |<--- setNumberVector -------------|   (3) Asynchronous updates (pushed by server)
  |<--- setSwitchVector -------------|       whenever ANY property changes
  |<--- setTextVector ---------------|
  |       ...                        |
  |                                  |
  |--- newNumberVector ------------->|   (4) Client requests a change
  |                                  |
  |<--- setNumberVector -------------|   (5) Server confirms with updated values
  |       ...                        |
  |                                  |
  |<--- deleteProperty --------------|   (6) Property removed (device unplugged, etc.)
```

**Key insight**: After the initial `getProperties` handshake, the server pushes ALL property updates automatically. The client does not need to re-request anything.

---

## 2. The INDIGO Bus and Callback Model

In the C reference implementation (`indigo_bus.c`), the bus maintains arrays of registered devices and clients. When a device calls `indigo_update_property()`, the bus iterates over ALL connected clients and invokes their `update_property` callback:

```c
// From indigo_bus.c — simplified
indigo_result indigo_update_property(indigo_device *device, indigo_property *property, ...) {
    for (int i = 0; i < MAX_CLIENTS; i++) {
        indigo_client *client = clients[i];
        if (client != NULL && client->update_property != NULL)
            client->update_property(client, device, property, message);
    }
}
```

The same pattern applies to `indigo_define_property()` → `client->define_property()` and `indigo_delete_property()` → `client->delete_property()`.

### Client Callbacks (C struct)

```c
static indigo_client my_client = {
    "MyClient",              // client name
    false,                   // is_remote
    NULL,                    // client_context
    INDIGO_OK,               // last_result
    INDIGO_VERSION_CURRENT,  // version
    NULL,                    // BLOB mode records
    my_attach,               // called on bus attach
    my_define_property,      // called when server sends defXXXVector
    my_update_property,      // called when server sends setXXXVector
    my_delete_property,      // called when server sends deleteProperty
    my_send_message,         // called when server sends a text message
    my_detach                // called on bus detach
};
```

### Over TCP: XML Adapter

When communicating over TCP, the C client XML adapter (`indigo_client_xml.c` + `indigo_xml.c`) acts as the bridge:

1. **Outgoing** (client → server): `indigo_enumerate_properties()` → writes `<getProperties .../>` XML to TCP socket
2. **Outgoing** (client → server): `indigo_change_property()` → writes `<newXXXVector ...>` XML to TCP socket
3. **Incoming** (server → client): XML parser reads TCP stream, parses `defXXXVector` → calls `indigo_define_property()` → dispatches to all `client->define_property()` callbacks
4. **Incoming** (server → client): XML parser reads TCP stream, parses `setXXXVector` → calls `indigo_update_property()` → dispatches to all `client->update_property()` callbacks
5. **Incoming** (server → client): XML parser reads TCP stream, parses `deleteProperty` → calls `indigo_delete_property()` → dispatches to all `client->delete_property()` callbacks

**The Python client must replicate this exact same behavior**: read XML from TCP, classify messages by tag, and dispatch them as typed events.

---

## 3. XML Protocol — Message Taxonomy

### 3.1 Client → Server Messages

| XML Tag | Purpose | INDIGO bus function |
|---------|---------|---------------------|
| `<getProperties version="2.0" />` | Request all property definitions | `indigo_enumerate_properties()` |
| `<getProperties device="X" name="Y" version="2.0" />` | Request specific property | `indigo_enumerate_properties()` |
| `<newTextVector device="X" name="Y">...</newTextVector>` | Change text property | `indigo_change_property()` |
| `<newNumberVector device="X" name="Y">...</newNumberVector>` | Change number property | `indigo_change_property()` |
| `<newSwitchVector device="X" name="Y">...</newSwitchVector>` | Change switch property | `indigo_change_property()` |
| `<enableBLOB device="X" name="Y">URL</enableBLOB>` | Enable BLOB transfer mode | `indigo_enable_blob()` |

### 3.2 Server → Client Messages

| XML Tag | Event Type | INDIGO callback | When sent |
|---------|-----------|-----------------|-----------|
| `<defTextVector ...>` | **DEFINE** | `client->define_property()` | Response to `getProperties`, or when a new property becomes available |
| `<defNumberVector ...>` | **DEFINE** | `client->define_property()` | Same |
| `<defSwitchVector ...>` | **DEFINE** | `client->define_property()` | Same |
| `<defLightVector ...>` | **DEFINE** | `client->define_property()` | Same |
| `<defBLOBVector ...>` | **DEFINE** | `client->define_property()` | Same |
| `<setTextVector ...>` | **UPDATE** | `client->update_property()` | Whenever property values change |
| `<setNumberVector ...>` | **UPDATE** | `client->update_property()` | Same |
| `<setSwitchVector ...>` | **UPDATE** | `client->update_property()` | Same |
| `<setLightVector ...>` | **UPDATE** | `client->update_property()` | Same |
| `<setBLOBVector ...>` | **UPDATE** | `client->update_property()` | Same |
| `<deleteProperty .../>` | **DELETE** | `client->delete_property()` | Property removed, device disconnected |
| `<message .../>` | **MESSAGE** | `client->send_message()` | Informational text message |

### 3.3 Critical Distinction: `def` vs `set`

Both `defXXXVector` and `setXXXVector` carry property values, but they have **different semantics**:

- **`defXXXVector`**: Property definition. Contains metadata (label, group, perm, rule, min, max, step, format) AND current values. Sent once when the property is first announced (response to `getProperties`, device connected, etc.).
- **`setXXXVector`**: Property update. Contains ONLY the updated item values and the new state. Sent every time a property changes.
- **`deleteProperty`**: Property removal. May specify `device` + `name` for a single property, or just `device` with empty name for ALL properties of that device.

**The Python parser MUST classify each incoming XML element into one of these three event types.**

### 3.4 Protocol Version Handshake

When the client sends `<getProperties version='2.0'/>`, it declares it speaks INDIGO protocol v2.0. This enables:
- `target` attribute on number items in `setNumberVector`
- URL-based BLOB transfer
- INDIGO property/item names (not legacy INDI names)

The server may respond with `<switchProtocol version='2.0'/>` to confirm. The Python client should handle this message gracefully (it's informational).

---

## 4. Complete TCP Session Lifecycle

### Phase 1: Connection and Handshake

```
1. Client opens TCP connection to host:port (default 7624)
2. Client sends: <getProperties version="2.0" />
3. Server responds with a FLOOD of defXXXVector messages for ALL devices and properties
```

### Phase 2: Steady State (Event-Driven)

```
4. Server sends setXXXVector whenever ANY property changes on ANY device
   - Exposure countdown: setNumberVector for CCD_EXPOSURE (state=Busy, decreasing value)
   - Temperature update: setNumberVector for CCD_TEMPERATURE
   - Connection change: setSwitchVector for CONNECTION
   - Exposure complete: setNumberVector for CCD_EXPOSURE (state=Ok, value=0)
   - Image ready: setBLOBVector for CCD_IMAGE (state=Ok, URL to image data)

5. Client sends newXXXVector to request changes → server responds with setXXXVector
```

### Phase 3: Device Lifecycle Events

```
6. Device plugged in: New defXXXVector messages for the new device's properties
7. Device unplugged: deleteProperty with device name and empty property name
8. Driver unloaded: deleteProperty for all affected devices
```

### Phase 4: Disconnection

```
9. Client closes TCP connection
   - OR server closes connection (server shutdown, network failure)
   - Client should implement reconnection with exponential backoff
```

### Important: No Silence Means No Changes

If the server sends nothing for a long time, it means nothing has changed. This is normal. The client should NOT interpret silence as an error or start polling. The TCP keepalive mechanism handles dead connections.

---

## 5. Property Model in Depth

### 5.1 Property States

| State | Meaning | Reliability of values |
|-------|---------|----------------------|
| `Idle` | Not initialized or not in use | Values may be uninitialized |
| `Ok` | Values are valid, operation complete | **Safe to read** |
| `Busy` | Operation in progress | Values are transient (e.g., exposure countdown) |
| `Alert` | Error occurred | Values may be invalid |

**Note**: In INDIGO v2.0, `Idle` state IS used (unlike legacy INDI where Idle is mapped to Ok). The Python client should handle all four states.

### 5.2 Number Property: `value` vs `target`

This is a critical INDIGO concept often missed by client implementations:

- **`value`**: The CURRENT reading from the hardware (e.g., current CCD temperature: +10°C)
- **`target`**: The REQUESTED value (e.g., requested CCD temperature: -20°C)

Example: Client requests cooling to -20°C. The server responds:

```xml
<setNumberVector device="CCD" name="CCD_TEMPERATURE" state="Busy">
    <oneNumber name="TEMPERATURE" target="-20">10</oneNumber>
</setNumberVector>
```

Here `target="-20"` is what was requested, and the text content `10` is the current temperature. As the cooler works:

```xml
<setNumberVector device="CCD" name="CCD_TEMPERATURE" state="Busy">
    <oneNumber name="TEMPERATURE" target="-20">5</oneNumber>
</setNumberVector>
<!-- ... more updates as temperature decreases ... -->
<setNumberVector device="CCD" name="CCD_TEMPERATURE" state="Ok">
    <oneNumber name="TEMPERATURE" target="-20">-20</oneNumber>
</setNumberVector>
```

The `target` attribute is provided in `setNumberVector` only in INDIGO protocol v2.0+. The `defNumberVector` also carries `target` in item definitions.

### 5.3 Switch Property Rules

| Rule | Behavior |
|------|----------|
| `OneOfMany` | Exactly one switch is ON at all times (radio buttons) |
| `AtMostOne` | Zero or one switch ON (optional radio) |
| `AnyOfMany` | Independent checkboxes |

Switch values in XML: `On` / `Off` (case sensitive in v2.0).

### 5.4 BLOB Properties

BLOB (Binary Large Object) properties carry image data. **Critical rules:**

1. BLOB updates are **NOT sent by default**. The client must explicitly enable them with `<enableBLOB>`.
2. In INDIGO v2.0, use URL mode: `<enableBLOB device="X" name="CCD_IMAGE">URL</enableBLOB>`
3. With URL mode, the server sends a URL reference instead of base64-encoded data.
4. The client must HTTP GET the URL to retrieve the raw binary data.
5. Data at the URL is available ONLY while the property is in `Ok` state.

For a LOCAL mode camera (saving to disk), BLOB handling may not be needed since the image path is communicated via `CCD_IMAGE_FILE`.

### 5.5 The `deleteProperty` Special Cases

From `indigo_xml.c`:

- `<deleteProperty device="X" name="Y"/>`: Delete a single specific property
- `<deleteProperty device="X"/>`: Delete ALL properties for device X (device removed)
- When property name is empty: means the entire device and all its resources should be invalidated

The Python client should handle both cases and clear its property cache accordingly.

---

## 6. Why the Current Python Client Fails

### 6.1 Problem Analysis

After studying the current `indigo_client.py` and `indigo_camera_adapter.py`, the following issues prevent proper event-driven operation:

#### Issue 1: No Distinction Between `def` and `set` XML Tags

The current `_parse_element()` method stores the XML tag name in `event["tag"]`, but `_update_cache()` in the camera adapter **completely ignores the tag**. It only checks `event["property"]`. This means:

- `defSwitchVector` (initial definition) and `setSwitchVector` (update) are treated identically
- `deleteProperty` events are not handled at all
- The client has no concept of property lifecycle (defined → updated → deleted)

#### Issue 2: The `target` Attribute Is Never Extracted

The `_parse_element()` method only extracts child element text content:

```python
for child in root:
    name = child.attrib.get("name", child.tag)
    child_values[name] = (child.text or "").strip()
```

This misses the `target` attribute on `<oneNumber>` elements. For number properties like `CCD_TEMPERATURE`, the `target` is the requested temperature and the text is the current temperature. Without `target`, the client cannot distinguish between current and requested values.

#### Issue 3: Polling Loop for CONNECTION State

The `_connection_refresh_loop()` sends `getProperties` every 2 seconds for CONNECTION:

```python
async def _connection_refresh_loop(self) -> None:
    while self._connected:
        await asyncio.sleep(_CONNECTION_REFRESH_INTERVAL)
        await self._client.request_property(self._device_name, _PROP_CONNECTION)
```

This is polling. In a correct event-driven design, `CONNECTION` state updates arrive automatically via `setSwitchVector`. The polling loop is unnecessary and contradicts INDIGO's design philosophy.

#### Issue 4: Fragile XML Buffer Parsing

The `_try_extract_element()` method uses simple string searching for closing tags. This approach can break with:
- CDATA sections
- Attribute values containing `>` or `</`
- Nested comments
- Large or multi-line attribute values

A more robust incremental XML parsing approach is needed.

#### Issue 5: No Property State Cache

The INDIGO C framework maintains a full property cache (`context->properties[]` in `indigo_xml.c`). When a `setXXXVector` arrives, the parser merges it with the cached property. The current Python client has no such cache — it extracts values from individual events without cross-referencing with property definitions.

#### Issue 6: No `deleteProperty` Handling

When a device disconnects or a driver is unloaded, the server sends `<deleteProperty device="X"/>`. The Python client does not handle this, so it may reference stale data from a disconnected device.

#### Issue 7: Protocol Handshake

The client sends `<getProperties version="2.0" />` which is valid, but does not handle `<switchProtocol version="2.0"/>` response from the server. This is mostly harmless but should be handled for robustness.

---

## 7. Target Architecture for the Python Client

### 7.1 Layer Diagram

```
┌──────────────────────────────────────────────────────┐
│              IndigoCameraAdapter                      │
│  (Domain adapter: translates INDIGO → CameraState)   │
│  Registers callbacks for specific device/properties   │
└────────────────┬─────────────────────────────────────┘
                 │ subscribe/callbacks
┌────────────────▼─────────────────────────────────────┐
│                IndigoClient                           │
│  (Infrastructure: TCP, XML parse, event dispatch)     │
│                                                       │
│  ┌──────────────────────────────────────────────┐    │
│  │  PropertyCache (dict[device][property])       │    │
│  │  Stores full property state for all devices   │    │
│  └──────────────────────────────────────────────┘    │
│                                                       │
│  ┌──────────────────────────────────────────────┐    │
│  │  CallbackDispatcher                           │    │
│  │  on_define_property(device, property)          │    │
│  │  on_update_property(device, property)          │    │
│  │  on_delete_property(device, property_name)     │    │
│  │  on_message(device, message)                   │    │
│  └──────────────────────────────────────────────┘    │
│                                                       │
│  ┌──────────────────────────────────────────────┐    │
│  │  XML Stream Parser (incremental)              │    │
│  │  Reads TCP, extracts XML elements,            │    │
│  │  classifies into def/set/del, emits typed     │    │
│  │  IndigoPropertyEvent objects                   │    │
│  └──────────────────────────────────────────────┘    │
│                                                       │
│  ┌──────────────────────────────────────────────┐    │
│  │  TCP Connection Manager                       │    │
│  │  asyncio streams, reconnection, keepalive     │    │
│  └──────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────┘
```

### 7.2 Data Flow

```
TCP bytes → XML Parser → IndigoPropertyEvent → PropertyCache update → Callback dispatch → Adapter cache
```

Every incoming XML element goes through:
1. **Parse**: Extract structured data from XML
2. **Classify**: Determine if it's a DEF, SET, DELETE, or MESSAGE
3. **Cache**: Update the central property cache
4. **Dispatch**: Call registered callbacks for interested subscribers

---

## 8. IndigoClient — Detailed Specification

### 8.1 Data Models

```python
from enum import Enum
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

class PropertyEventType(Enum):
    DEFINE = "define"     # defXXXVector
    UPDATE = "update"     # setXXXVector
    DELETE = "delete"     # deleteProperty
    MESSAGE = "message"   # message

class PropertyType(Enum):
    TEXT = "text"
    NUMBER = "number"
    SWITCH = "switch"
    LIGHT = "light"
    BLOB = "blob"

class PropertyState(Enum):
    IDLE = "Idle"
    OK = "Ok"
    BUSY = "Busy"
    ALERT = "Alert"

class PropertyPerm(Enum):
    RO = "ro"
    RW = "rw"
    WO = "wo"

@dataclass
class IndigoItem:
    """Single property item with all relevant attributes."""
    name: str
    label: str = ""
    # For text items
    value_text: str = ""
    # For number items
    value_number: float = 0.0
    target_number: float = 0.0
    min_number: float = 0.0
    max_number: float = 0.0
    step_number: float = 0.0
    format_number: str = ""
    # For switch items
    value_switch: bool = False
    # For light items
    value_light: PropertyState = PropertyState.IDLE
    # For blob items
    value_blob_url: str = ""
    value_blob_format: str = ""

@dataclass
class IndigoProperty:
    """Full representation of an INDIGO property."""
    device: str
    name: str
    type: PropertyType
    state: PropertyState = PropertyState.IDLE
    perm: PropertyPerm = PropertyPerm.RO
    group: str = ""
    label: str = ""
    rule: str = ""   # For switch properties: OneOfMany, AtMostOne, AnyOfMany
    items: dict[str, IndigoItem] = field(default_factory=dict)
    message: str = ""

@dataclass
class IndigoEvent:
    """Event emitted by the XML parser."""
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
    raw_tag: str = ""  # Original XML tag name for debugging
```

### 8.2 Property Cache

```python
class PropertyCache:
    """Thread-safe cache of all known INDIGO properties."""

    def __init__(self) -> None:
        # Key: (device_name, property_name) → IndigoProperty
        self._properties: dict[tuple[str, str], IndigoProperty] = {}

    def handle_define(self, event: IndigoEvent) -> IndigoProperty:
        """Process a defXXXVector event. Creates or replaces the property."""
        prop = IndigoProperty(
            device=event.device,
            name=event.property_name,
            type=event.property_type,
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

    def handle_update(self, event: IndigoEvent) -> IndigoProperty | None:
        """Process a setXXXVector event. Merges with existing property."""
        key = (event.device, event.property_name)
        prop = self._properties.get(key)
        if prop is None:
            # setXXXVector without prior defXXXVector — create minimal entry
            prop = IndigoProperty(
                device=event.device,
                name=event.property_name,
                type=event.property_type or PropertyType.TEXT,
            )
            self._properties[key] = prop

        # Update state
        if event.state is not None:
            prop.state = event.state
        if event.message:
            prop.message = event.message

        # Merge item values (set only updates the items it carries)
        for item_name, item in event.items.items():
            if item_name in prop.items:
                existing = prop.items[item_name]
                # Update values from the event
                if prop.type == PropertyType.NUMBER:
                    existing.value_number = item.value_number
                    if item.target_number != 0.0 or existing.target_number == 0.0:
                        existing.target_number = item.target_number
                elif prop.type == PropertyType.SWITCH:
                    existing.value_switch = item.value_switch
                elif prop.type == PropertyType.TEXT:
                    existing.value_text = item.value_text
                elif prop.type == PropertyType.LIGHT:
                    existing.value_light = item.value_light
                elif prop.type == PropertyType.BLOB:
                    existing.value_blob_url = item.value_blob_url
                    existing.value_blob_format = item.value_blob_format
            else:
                prop.items[item_name] = item

        return prop

    def handle_delete(self, event: IndigoEvent) -> list[str]:
        """Process a deleteProperty event. Returns names of deleted properties."""
        deleted = []
        if event.property_name:
            # Delete specific property
            key = (event.device, event.property_name)
            if key in self._properties:
                del self._properties[key]
                deleted.append(event.property_name)
        else:
            # Delete ALL properties for this device
            keys_to_delete = [
                k for k in self._properties if k[0] == event.device
            ]
            for k in keys_to_delete:
                deleted.append(k[1])
                del self._properties[k]
        return deleted

    def get(self, device: str, name: str) -> IndigoProperty | None:
        return self._properties.get((device, name))

    def get_device_properties(self, device: str) -> list[IndigoProperty]:
        return [p for k, p in self._properties.items() if k[0] == device]
```

### 8.3 XML Parser Requirements

The XML parser must:

1. **Parse incrementally**: Read TCP data in chunks, accumulate in a buffer, extract complete XML elements.

2. **Classify each element** by its root tag:

| Root tag prefix | Event type |
|-----------------|------------|
| `def*Vector` | DEFINE |
| `set*Vector` | UPDATE |
| `deleteProperty` | DELETE |
| `message` | MESSAGE |
| `switchProtocol` | (internal, not dispatched) |
| `getProperties` | (should not arrive at client, ignore) |
| `new*Vector` | (should not arrive at client, ignore) |

3. **Extract all attributes** from the root element:
   - `device`, `name`, `state`, `perm`, `group`, `label`, `rule`, `message`

4. **Extract items** from child elements, including:
   - **For `oneNumber`**: `name` attribute, `target` attribute, `min`/`max`/`step`/`format` attributes (in def), text content = value
   - **For `oneSwitch`**: `name` attribute, text content = `On`/`Off`
   - **For `oneText`**: `name` attribute, text content = value
   - **For `oneLight`**: `name` attribute, text content = state
   - **For `oneBLOB`**: `name` attribute, `url` attribute, `format` attribute

5. **Handle `switchProtocol`**: When received, note the negotiated version (informational).

#### Mapping XML Tags to PropertyType

```python
TAG_TO_PROPERTY_TYPE = {
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

TAG_TO_EVENT_TYPE = {
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
```

#### Parsing `oneNumber` Items — Critical Detail

```xml
<!-- In setNumberVector (update): -->
<oneNumber name="TEMPERATURE" target="-20">10.5</oneNumber>
```

Must extract:
- `name` = "TEMPERATURE" (from attribute)
- `target` = -20.0 (from `target` attribute)
- `value` = 10.5 (from text content)

```xml
<!-- In defNumberVector (definition): -->
<defNumber name="TEMPERATURE" label="Temperature" format="%g"
           min="-50" max="50" step="1" target="-20">10.5</defNumber>
```

Must extract all of: `name`, `label`, `format`, `min`, `max`, `step`, `target`, and the text content as `value`.

### 8.4 Callback Registration System

```python
# Type alias for callbacks
PropertyCallback = Callable[[IndigoProperty], Awaitable[None]]
DeleteCallback = Callable[[str, str], Awaitable[None]]  # (device, property_name)

class IndigoClient:
    """Event-driven INDIGO TCP client."""

    def __init__(self) -> None:
        self._property_cache = PropertyCache()
        # Callbacks: list of (device_filter, property_filter, callback)
        # None filter = match all
        self._define_callbacks: list[tuple[str | None, str | None, PropertyCallback]] = []
        self._update_callbacks: list[tuple[str | None, str | None, PropertyCallback]] = []
        self._delete_callbacks: list[tuple[str | None, str | None, DeleteCallback]] = []

    def on_define(
        self,
        callback: PropertyCallback,
        device: str | None = None,
        property_name: str | None = None,
    ) -> None:
        """Register callback for property definition events."""
        self._define_callbacks.append((device, property_name, callback))

    def on_update(
        self,
        callback: PropertyCallback,
        device: str | None = None,
        property_name: str | None = None,
    ) -> None:
        """Register callback for property update events."""
        self._update_callbacks.append((device, property_name, callback))

    def on_delete(
        self,
        callback: DeleteCallback,
        device: str | None = None,
        property_name: str | None = None,
    ) -> None:
        """Register callback for property deletion events."""
        self._delete_callbacks.append((device, property_name, callback))
```

### 8.5 Event Dispatch Logic

```python
async def _dispatch_event(self, event: IndigoEvent) -> None:
    """Process a parsed event: update cache and invoke callbacks."""

    if event.event_type == PropertyEventType.DEFINE:
        prop = self._property_cache.handle_define(event)
        for dev_filter, prop_filter, cb in self._define_callbacks:
            if self._matches(event.device, event.property_name, dev_filter, prop_filter):
                await cb(prop)

    elif event.event_type == PropertyEventType.UPDATE:
        prop = self._property_cache.handle_update(event)
        if prop is not None:
            for dev_filter, prop_filter, cb in self._update_callbacks:
                if self._matches(event.device, event.property_name, dev_filter, prop_filter):
                    await cb(prop)

    elif event.event_type == PropertyEventType.DELETE:
        deleted = self._property_cache.handle_delete(event)
        for name in deleted:
            for dev_filter, prop_filter, cb in self._delete_callbacks:
                if self._matches(event.device, name, dev_filter, prop_filter):
                    await cb(event.device, name)

@staticmethod
def _matches(device: str, prop_name: str, dev_filter: str | None, prop_filter: str | None) -> bool:
    if dev_filter is not None and dev_filter != device:
        return False
    if prop_filter is not None and prop_filter != prop_name:
        return False
    return True
```

### 8.6 Connection Lifecycle

```python
async def connect(self, host: str, port: int) -> None:
    """Connect to INDIGO server."""
    self._host = host
    self._port = port
    self._reader, self._writer = await asyncio.open_connection(host, port)
    self._connected = True

    # Start background read loop
    self._read_task = asyncio.create_task(self._read_loop())

    # Send initial getProperties handshake (INDIGO v2.0)
    await self._send_raw('<getProperties version="2.0" />')

    # The server will now flood us with defXXXVector messages.
    # Our _read_loop will parse them and dispatch to callbacks.
    # NO POLLING NEEDED after this point.
```

### 8.7 Sending Commands

The existing `send_number()`, `send_switch()`, `send_text()` methods are correct in their XML generation. They should be preserved. The only addition needed is `enable_blob()`:

```python
async def enable_blob(self, device: str, name: str, mode: str = "URL") -> None:
    """Enable BLOB transfer for a property.

    Args:
        device: Device name.
        name: Property name (e.g., 'CCD_IMAGE').
        mode: 'URL' (recommended), 'Also' (base64), or 'Never'.
    """
    xml = f'<enableBLOB device="{device}" name="{name}">{mode}</enableBLOB>'
    await self._send_raw(xml)
```

### 8.8 Reconnection

The current reconnection logic is acceptable. On reconnect, `_establish_connection()` should:
1. Open TCP connection
2. Reset XML buffer
3. Start read loop
4. Send `<getProperties version="2.0" />`

The server will re-send all `defXXXVector` messages, fully repopulating the property cache.

---

## 9. IndigoCameraAdapter — Detailed Specification

### 9.1 Registration Pattern

Instead of a generic `event_stream()` iterator, the adapter registers specific callbacks:

```python
class IndigoCameraAdapter(ICameraDevice):

    async def connect(self, host: str, port: int) -> None:
        await self._client.connect(host, port)
        self._connected = True

        # Register for property events on our specific device
        dev = self._device_name

        self._client.on_define(self._on_property_defined, device=dev)
        self._client.on_update(self._on_property_updated, device=dev)
        self._client.on_delete(self._on_property_deleted, device=dev)

    async def _on_property_defined(self, prop: IndigoProperty) -> None:
        """Handle initial property definitions."""
        # When CONNECTION is defined, check if already connected
        if prop.name == "CONNECTION":
            connected_item = prop.items.get("CONNECTED")
            if connected_item and connected_item.value_switch:
                self._device_connected = True
            else:
                # Device not connected — request connection
                await self.connect_device()

        # When CCD_IMAGE is defined, enable BLOB URL mode
        if prop.name == "CCD_IMAGE":
            await self._client.enable_blob(self._device_name, "CCD_IMAGE", "URL")

        # Also process initial values (def carries current values)
        self._apply_property_to_cache(prop)

    async def _on_property_updated(self, prop: IndigoProperty) -> None:
        """Handle property value updates — the CORE of event-driven operation."""
        self._apply_property_to_cache(prop)

    async def _on_property_deleted(self, device: str, property_name: str) -> None:
        """Handle property deletion (device disconnected, etc.)."""
        if not property_name:
            # All properties deleted — device removed
            self._device_connected = False
            self._reset_cache()
```

### 9.2 Cache Update Method

```python
def _apply_property_to_cache(self, prop: IndigoProperty) -> None:
    """Update local camera state from an INDIGO property."""
    self._last_update = _now_utc()

    if prop.name == "CONNECTION":
        item = prop.items.get("CONNECTED")
        if item is not None:
            self._device_connected = item.value_switch

    elif prop.name == "CCD_EXPOSURE":
        self._exposure_state = prop.state.value if prop.state else "IDLE"
        item = prop.items.get("EXPOSURE")
        if item is not None:
            self._exposure_value = item.value_number
            # target_number gives the originally requested exposure time
            if item.target_number > 0:
                self._exposure_target = item.target_number

    elif prop.name == "CCD_TEMPERATURE":
        item = prop.items.get("TEMPERATURE")
        if item is not None:
            self._ccd_temp_c = item.value_number
            # target is the requested temperature
            if prop.state == PropertyState.OK or prop.state == PropertyState.BUSY:
                self._cooler_target_c = item.target_number

    elif prop.name == "CCD_COOLER":
        item = prop.items.get("ON")
        if item is not None:
            self._cooler_on = item.value_switch

    elif prop.name == "CCD_COOLER_POWER":
        item = prop.items.get("POWER")
        if item is not None:
            self._cooler_power_pct = item.value_number
        self._derive_cooler_state()

    elif prop.name == "CCD_BIN":
        h = prop.items.get("HORIZONTAL")
        v = prop.items.get("VERTICAL")
        if h is not None:
            self._bin_x = int(h.value_number)
        if v is not None:
            self._bin_y = int(v.value_number)

    elif prop.name == "CCD_FRAME":
        self._roi = (
            int(prop.items.get("LEFT", IndigoItem(name="")).value_number),
            int(prop.items.get("TOP", IndigoItem(name="")).value_number),
            int(prop.items.get("WIDTH", IndigoItem(name="")).value_number),
            int(prop.items.get("HEIGHT", IndigoItem(name="")).value_number),
        )

    elif prop.name == "CCD_GAIN":
        item = prop.items.get("GAIN")
        if item is not None:
            self._gain = int(item.value_number)

    elif prop.name == "CCD_OFFSET":
        item = prop.items.get("OFFSET")
        if item is not None:
            self._offset = int(item.value_number)

    elif prop.name == "CCD_IMAGE":
        item = prop.items.get("IMAGE")
        if item is not None and item.value_blob_url:
            self._last_image_path = item.value_blob_url

    elif prop.name == "CCD_IMAGE_FILE":
        item = prop.items.get("FILE")
        if item is not None and item.value_text:
            self._last_image_path = item.value_text

    elif prop.name == "CCD_LOCAL_MODE":
        item = prop.items.get("DIR")
        if item is not None and item.value_text:
            self._local_mode_dir = item.value_text
```

### 9.3 Eliminating the Polling Loop

The `_connection_refresh_loop()` method should be **completely removed**. The CONNECTION property is automatically pushed by the server whenever it changes. The auto-connect logic can be moved to the `_on_property_defined` callback (attempt connection if DISCONNECTED upon first definition).

If auto-reconnect of the camera device is desired after an external disconnect, it can be handled in `_on_property_updated` for the CONNECTION property:

```python
async def _on_property_updated(self, prop: IndigoProperty) -> None:
    self._apply_property_to_cache(prop)

    # Auto-reconnect: if CONNECTION shows disconnected and we want it connected
    if prop.name == "CONNECTION" and self._auto_connect:
        connected_item = prop.items.get("CONNECTED")
        if connected_item and not connected_item.value_switch:
            # Device became disconnected — schedule reconnect
            if self._auto_connect_enabled:
                await asyncio.sleep(5)
                await self.connect_device()
```

---

## 10. XML Parsing Rules and Edge Cases

### 10.1 Incremental Parsing Strategy

The recommended approach is to keep using `xml.etree.ElementTree.fromstring()` for parsing complete elements, but improve the buffer extraction logic:

1. Read TCP data in chunks (e.g., 64 KB)
2. Append to buffer (decoded UTF-8)
3. Attempt to extract complete top-level XML elements
4. For each complete element, parse with `ET.fromstring()` and classify

### 10.2 Robust Element Extraction

The current `_try_extract_element()` approach of string-searching for closing tags is acceptable for INDIGO's simple XML format (no nesting of vector elements), but should be hardened:

- Handle self-closing tags: `<deleteProperty device="X" name="Y"/>`
- Handle the `<message .../>` self-closing tag
- Handle `<switchProtocol .../>` self-closing tag
- Ignore any leading whitespace or newlines between elements
- Handle the case where a single TCP read contains multiple complete elements

### 10.3 XML Entity Escaping

INDIGO device names may contain special characters (e.g., `"CCD Imager Simulator @ indigosky"`). The `@` is fine, but `&`, `<`, `>`, `'`, `"` in attribute values are XML-escaped. Python's `ET.fromstring()` handles this automatically.

### 10.4 Large Messages

BLOB properties with base64-encoded data can be very large (megabytes). If using URL mode, this is not an issue since only a URL string is transmitted. If base64 mode is used, the buffer may need to handle multi-megabyte accumulation.

---

## 11. Implementation Checklist

### Phase 1: IndigoClient Refactoring

- [ ] Define data models: `IndigoItem`, `IndigoProperty`, `IndigoEvent`, `PropertyEventType`, `PropertyType`, `PropertyState`, `PropertyPerm`
- [ ] Implement `PropertyCache` with `handle_define()`, `handle_update()`, `handle_delete()` methods
- [ ] Refactor `_parse_element()` to produce `IndigoEvent` objects with:
  - Proper event type classification (DEF/SET/DEL/MSG)
  - Full item attribute extraction (including `target` for numbers)
  - Property metadata extraction (perm, group, label, rule)
- [ ] Implement callback registration: `on_define()`, `on_update()`, `on_delete()`
- [ ] Implement `_dispatch_event()` to update cache and fire callbacks
- [ ] Replace the `event_stream()` AsyncIterator with the callback model
- [ ] Add `enable_blob()` method
- [ ] Handle `switchProtocol` and `message` XML elements
- [ ] Keep existing `send_number()`, `send_switch()`, `send_text()`, `request_property()` methods
- [ ] Keep existing reconnection logic

### Phase 2: IndigoCameraAdapter Refactoring

- [ ] Replace `_cache_updater()` (event_stream iterator) with callback registration
- [ ] Implement `_on_property_defined()`: handle initial property definitions, auto-connect, enable BLOB
- [ ] Implement `_on_property_updated()`: update camera state cache
- [ ] Implement `_on_property_deleted()`: clear state when device removed
- [ ] Refactor `_apply_property_to_cache()` to use typed `IndigoProperty` objects instead of raw dicts
- [ ] **Remove `_connection_refresh_loop()`** — no more polling
- [ ] Use `target_number` for CCD_TEMPERATURE and CCD_EXPOSURE instead of guessing from text values
- [ ] Handle `CCD_IMAGE_FILE` property for local mode image paths
- [ ] Handle device auto-reconnect via property update callback (not polling)

### Phase 3: Testing

- [ ] Unit test XML parsing for all message types (def, set, del, message)
- [ ] Unit test property cache merge logic
- [ ] Unit test callback dispatch with device/property filters
- [ ] Integration test with INDIGO simulator server
- [ ] Verify no polling loops remain

---

## Appendix A: Complete XML Message Examples

### A.1 defSwitchVector (CONNECTION property)

```xml
<defSwitchVector device="CCD Imager Simulator" name="CONNECTION"
    group="Main" label="Connection" rule="OneOfMany" state="Ok" perm="rw">
    <defSwitch name="CONNECTED" label="Connected">Off</defSwitch>
    <defSwitch name="DISCONNECTED" label="Disconnected">On</defSwitch>
</defSwitchVector>
```

### A.2 setSwitchVector (CONNECTION changed)

```xml
<setSwitchVector device="CCD Imager Simulator" name="CONNECTION" state="Ok">
    <oneSwitch name="CONNECTED">On</oneSwitch>
    <oneSwitch name="DISCONNECTED">Off</oneSwitch>
</setSwitchVector>
```

### A.3 defNumberVector (CCD_EXPOSURE definition)

```xml
<defNumberVector device="CCD Imager Simulator" name="CCD_EXPOSURE"
    group="Camera" label="Start exposure" state="Idle" perm="rw">
    <defNumber name="EXPOSURE" label="Start exposure"
        min="0" max="10000" step="1" format="%g" target="0">0</defNumber>
</defNumberVector>
```

### A.4 setNumberVector (Exposure in progress)

```xml
<setNumberVector device="CCD Imager Simulator" name="CCD_EXPOSURE" state="Busy">
    <oneNumber name="EXPOSURE" target="3">2.5</oneNumber>
</setNumberVector>
```

### A.5 setNumberVector (Exposure complete)

```xml
<setNumberVector device="CCD Imager Simulator" name="CCD_EXPOSURE" state="Ok">
    <oneNumber name="EXPOSURE" target="3">0</oneNumber>
</setNumberVector>
```

### A.6 setNumberVector (Temperature with target)

```xml
<setNumberVector device="CCD Imager Simulator" name="CCD_TEMPERATURE" state="Busy">
    <oneNumber name="TEMPERATURE" target="-20">5.3</oneNumber>
</setNumberVector>
```

### A.7 setBLOBVector (Image ready, URL mode)

```xml
<setBLOBVector device="CCD Imager Simulator" name="CCD_IMAGE" state="Ok">
    <oneBLOB name="IMAGE" url="http://localhost:7624/blob/0x10381d798.fits"/>
</setBLOBVector>
```

### A.8 deleteProperty (Device removed)

```xml
<deleteProperty device="CCD Imager Simulator"/>
```

### A.9 deleteProperty (Specific property removed)

```xml
<deleteProperty device="CCD Imager Simulator" name="CCD_STREAMING"/>
```

### A.10 message

```xml
<message device="CCD Imager Simulator" timestamp="2026-02-14T12:00:00"
    message="Exposure complete"/>
```

---

## Appendix B: Summary of Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Push-based callbacks instead of event_stream iterator | Matches INDIGO C architecture; enables filtered, typed event handling |
| Central PropertyCache | Mirrors `context->properties[]` in C; allows merging set updates with def definitions |
| Typed data models (IndigoProperty, IndigoItem) | Eliminates stringly-typed dict access; enables IDE autocomplete and type checking |
| Extract `target` attribute from oneNumber | Critical for CCD_TEMPERATURE and CCD_EXPOSURE; distinguishes requested vs current value |
| Remove all polling loops | Follows INDIGO design philosophy; reduces server load; eliminates race conditions |
| Classify XML by tag (def/set/del) | Matches the three client callbacks in C; enables proper property lifecycle management |
| Handle deleteProperty | Prevents stale state when devices are removed |
| Enable BLOB via URL mode on property define | INDIGO v2.0 best practice; avoids base64 overhead |

---

## Appendix C: Reference Files

| File | Purpose |
|------|---------|
| `indigo_libs/indigo_bus.c` | Bus implementation, message dispatching to callbacks |
| `indigo_libs/indigo_client.c` | Client connection management, TCP server threads |
| `indigo_libs/indigo_client_xml.c` | XML adapter: translates bus calls to XML over TCP |
| `indigo_libs/indigo_xml.c` | XML parser: translates incoming XML to bus calls |
| `indigo_docs/CLIENT_DEVELOPMENT_BASICS.md` | Official client development guide |
| `indigo_docs/PROTOCOLS.md` | XML/JSON protocol specification |
| `indigo_docs/PROPERTIES.md` | Standard property definitions |
| `indigo_docs/PROPERTY_MANIPULATION.md` | Command-line examples of property manipulation |
