"""Telemetry WebSocket route."""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from src.domain.ports.telemetry import ITelemetryPublisher
from src.shared.config import Config

router = APIRouter(prefix="/api/v1/ws", tags=["telemetry"])


def _is_valid_api_key(config: Config, api_key: str | None) -> bool:
    if not config.get("auth.enabled", False):
        return True
    if not api_key:
        return False
    for key_entry in config.get("auth.keys", []):
        if key_entry.get("key") == api_key:
            return True
    return False


@router.websocket("/telemetry")
async def telemetry_ws(
    websocket: WebSocket,
) -> None:
    """Register telemetry websocket client with validated cadence."""
    config: Config = websocket.app.state.config
    telemetry_publisher: ITelemetryPublisher = websocket.app.state.telemetry_publisher

    raw_rate = websocket.query_params.get(
        "rate_hz", str(int(config.get("telemetry.default_rate_hz", 1)))
    )
    try:
        rate_hz = int(raw_rate)
    except ValueError:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid rate_hz")
        return

    if rate_hz not in {1, 2}:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="rate_hz must be 1 or 2")
        return

    api_key = websocket.headers.get("x-api-key")
    if not _is_valid_api_key(config, api_key):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid API key")
        return

    connected = await telemetry_publisher.connect(websocket, rate_hz)  # type: ignore[attr-defined]
    if not connected:
        await websocket.close(code=status.WS_1013_TRY_AGAIN_LATER, reason="Max clients reached")
        return

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        telemetry_publisher.disconnect(websocket)  # type: ignore[attr-defined]
