"""Integration tests for INDIGO camera adapter."""

from __future__ import annotations

import asyncio
import os

import pytest

from src.infrastructure.adapters.indigo_camera_adapter import IndigoCameraAdapter

RUN_INTEGRATION = os.getenv("CAVEX_RUN_INDIGO_INTEGRATION", "false").lower() == "true"

pytestmark = pytest.mark.skipif(
    not RUN_INTEGRATION,
    reason="Set CAVEX_RUN_INDIGO_INTEGRATION=true to run INDIGO integration tests.",
)


def _connection_params() -> tuple[str, int, str]:
    """Read integration connection parameters from environment."""
    host = os.getenv("INDIGO_HOST", "localhost")
    port = int(os.getenv("INDIGO_PORT", "7624"))
    device_name = os.getenv("INDIGO_DEVICE_NAME", "CCD Imager Simulator")
    return host, port, device_name


@pytest.mark.asyncio
async def test_connection_to_indigo_simulator_server() -> None:
    """Test adapter can connect and disconnect to INDIGO simulator."""
    host, port, device_name = _connection_params()
    adapter = IndigoCameraAdapter(device_name=device_name)

    await adapter.connect(host, port)
    state = adapter.get_state()
    assert state.indigo_connected is True
    await adapter.disconnect()


@pytest.mark.asyncio
async def test_connect_device() -> None:
    """Test adapter can connect and disconnect the device."""
    host, port, device_name = _connection_params()
    adapter = IndigoCameraAdapter(device_name=device_name)

    await adapter.connect(host, port)
    await adapter.connect_device()
    await asyncio.sleep(1)  # Allow events to propagate
    state = adapter.get_state()
    await adapter.disconnect()

    assert state.device_name == device_name


@pytest.mark.asyncio
async def test_start_and_abort_exposure() -> None:
    """Test adapter starts and aborts exposure."""
    host, port, device_name = _connection_params()
    adapter = IndigoCameraAdapter(device_name=device_name)

    await adapter.connect(host, port)
    await adapter.connect_device()
    await asyncio.sleep(1)

    await adapter.start_exposure(exptime_s=10.0)
    await asyncio.sleep(0.5)
    await adapter.abort_exposure()

    await adapter.disconnect()


@pytest.mark.asyncio
async def test_get_state_returns_snapshot() -> None:
    """Test adapter get_state returns a valid snapshot."""
    host, port, device_name = _connection_params()
    adapter = IndigoCameraAdapter(device_name=device_name)

    await adapter.connect(host, port)
    state = adapter.get_state()
    await adapter.disconnect()

    assert state.device_name == device_name
    assert state.indigo_connected is True
