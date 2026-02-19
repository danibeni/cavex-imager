#!/usr/bin/env python3
"""Smoke test for the event-driven INDIGO client.

Connects to an INDIGO server, registers define/update/delete callbacks,
and prints received events for a few seconds. Use to verify the client
works against a real server (e.g. CCD Imager Simulator on port 7624).

Usage:
    # From project root, with INDIGO server running:
    poetry run python scripts/verify_indigo_client.py

    # Override host/port:
    INDIGO_HOST=192.168.1.10 INDIGO_PORT=7624 poetry run python scripts/verify_indigo_client.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Ensure project root is on path when run as script
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from src.infrastructure.adapters.indigo_client import (
    IndigoClient,
    IndigoProperty,
    PropertyEventType,
    PropertyState,
)


def _summary(prop: IndigoProperty) -> str:
    """Short summary of a property for console output."""
    parts = [f"state={prop.state.name}"]
    for name, item in list(prop.items.items())[:3]:
        if prop.type.value == "number":
            parts.append(f"{name}={item.value_number}")
            if item.target_number != 0.0:
                parts.append(f"target={item.target_number}")
        elif prop.type.value == "switch":
            parts.append(f"{name}={item.value_switch}")
        else:
            parts.append(f"{name}={item.value_text or item.value_blob_url or '...'}")
    return " ".join(parts)


async def main() -> int:
    host = os.environ.get("INDIGO_HOST", "localhost")
    port = int(os.environ.get("INDIGO_PORT", "7624"))
    duration_s = float(os.environ.get("INDIGO_VERIFY_DURATION", "5"))

    define_count = 0
    update_count = 0
    delete_count = 0

    async def on_define(prop: IndigoProperty) -> None:
        nonlocal define_count
        define_count += 1
        if define_count <= 15:  # Limit noise
            print(f"  DEFINE {prop.device} {prop.name} ({_summary(prop)})")
        elif define_count == 16:
            print("  DEFINE ... (further defines omitted)")

    async def on_update(prop: IndigoProperty) -> None:
        nonlocal update_count
        update_count += 1
        if update_count <= 20:
            print(f"  UPDATE {prop.device} {prop.name} ({_summary(prop)})")
        elif update_count == 21:
            print("  UPDATE ... (further updates omitted)")

    async def on_delete(device: str, property_name: str) -> None:
        nonlocal delete_count
        delete_count += 1
        print(f"  DELETE {device} {property_name or '(all)'}")

    client = IndigoClient()
    client.on_define(on_define)
    client.on_update(on_update)
    client.on_delete(on_delete)

    print(f"Connecting to {host}:{port} for {duration_s}s ...")
    try:
        await client.connect(host, port)
    except Exception as e:
        print(f"Connection failed: {e}")
        print("Ensure INDIGO server is running (e.g. indigo_sky with CCD Imager Simulator).")
        return 1

    print("Connected. Waiting for events (definitions first, then updates) ...")
    await asyncio.sleep(duration_s)

    await client.disconnect()
    print(f"\nDone. Received: {define_count} define(s), {update_count} update(s), {delete_count} delete(s).")

    if define_count == 0:
        print("Warning: no DEFINE events — server may not have sent getProperties response.")
        return 1
    print("OK: client received events from INDIGO server.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
