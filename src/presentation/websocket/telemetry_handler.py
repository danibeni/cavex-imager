"""Telemetry WebSocket handler and publisher."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketState

from src.domain.ports.camera_device import ICameraDevice
from src.domain.ports.telemetry import ITelemetryPublisher
from src.shared.observability import ws_clients_connected

logger = logging.getLogger(__name__)


class TelemetryHandler(ITelemetryPublisher):
    """Manage telemetry WebSocket clients and outgoing messages."""

    def __init__(
        self,
        camera: ICameraDevice,
        telemetry_publisher: ITelemetryPublisher | None = None,
        max_clients: int = 10,
    ) -> None:
        """Initialize handler.

        Args:
            camera: Camera device source for snapshots.
            telemetry_publisher: Unused, kept for spec-compatible signature.
            max_clients: Maximum connected clients.
        """
        self._camera = camera
        self._max_clients = max_clients
        self._clients: list[WebSocket] = []
        self._rate_by_client: dict[WebSocket, int] = {}
        self._tick = 0
        _ = telemetry_publisher

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _envelope(self, message_type: str, data: dict[str, Any]) -> dict[str, Any]:
        return {"type": message_type, "timestamp": self._utc_now_iso(), "data": data}

    def _snapshot_data(self) -> dict[str, Any]:
        state = self._camera.get_state()
        return {
            "device": {
                "name": state.device_name,
                "connected": state.connected,
            },
            "status": {
                "state": state.exposure_state,
                "exposure": {
                    "is_exposing": state.exposure_state.upper() in {"BUSY", "ALERT"},
                    "elapsed_s": state.exposure_value,
                    "total_s": state.exposure_target,
                    "progress_pct": (
                        (state.exposure_value / state.exposure_target) * 100.0
                        if state.exposure_target > 0
                        else 0.0
                    ),
                },
                "thermal": {
                    "ccd_temp_c": state.ccd_temp_c,
                    "target_temp_c": state.cooler_target_c,
                    "cooler_on": state.cooler_on,
                    "cooler_power_pct": state.cooler_power_pct,
                    "cooler_state": state.cooler_state,
                },
            },
            "config": {
                "binning": [state.bin_x, state.bin_y],
                "roi": list(state.roi),
                "gain": state.gain,
                "offset": state.offset,
            },
        }

    async def connect(self, websocket: WebSocket, rate_hz: int) -> bool:
        """Register a WebSocket client if allowed."""
        if len(self._clients) >= self._max_clients:
            logger.warning("telemetry_ws_rejected_capacity max_clients=%s", self._max_clients)
            return False
        if rate_hz not in {1, 2}:
            logger.warning("telemetry_ws_rejected_rate rate_hz=%s", rate_hz)
            return False

        await websocket.accept()
        self._clients.append(websocket)
        self._rate_by_client[websocket] = rate_hz
        ws_clients_connected.set(len(self._clients))
        logger.info("telemetry_ws_connected clients=%s rate_hz=%s", len(self._clients), rate_hz)
        return True

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove client from active connection registry."""
        if websocket in self._clients:
            self._clients.remove(websocket)
        self._rate_by_client.pop(websocket, None)
        ws_clients_connected.set(len(self._clients))
        logger.info("telemetry_ws_disconnected clients=%s", len(self._clients))

    async def broadcast_snapshot(self) -> None:
        """Broadcast periodic telemetry snapshots respecting per-client rate."""
        if not self._clients:
            return

        self._tick += 1
        payload = self._envelope("telemetry_snapshot", self._snapshot_data())

        to_remove: list[WebSocket] = []
        for websocket in list(self._clients):
            rate_hz = self._rate_by_client.get(websocket, 1)
            if rate_hz == 1 and self._tick % 2 != 0:
                continue
            if websocket.application_state != WebSocketState.CONNECTED:
                to_remove.append(websocket)
                continue
            try:
                await websocket.send_json(payload)
            except Exception:
                to_remove.append(websocket)

        for websocket in to_remove:
            self.disconnect(websocket)

    async def publish_snapshot(self, snapshot: dict[str, Any]) -> None:
        """Broadcast an externally provided snapshot payload."""
        await self._broadcast_json(self._envelope("telemetry_snapshot", snapshot))

    async def publish_event(self, event_type: str, payload: dict[str, Any]) -> None:
        """Broadcast a discrete telemetry event."""
        await self._broadcast_json(self._envelope(event_type, payload))

    async def _broadcast_json(self, message: dict[str, Any]) -> None:
        to_remove: list[WebSocket] = []
        for websocket in list(self._clients):
            if websocket.application_state != WebSocketState.CONNECTED:
                to_remove.append(websocket)
                continue
            try:
                await websocket.send_json(message)
            except Exception:
                to_remove.append(websocket)
        for websocket in to_remove:
            self.disconnect(websocket)

    def client_count(self) -> int:
        """Return number of connected clients."""
        return len(self._clients)
