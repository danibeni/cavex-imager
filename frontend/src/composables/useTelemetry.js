/**
 * Telemetry Composable
 * Handles WebSocket connection, selective subscription, and auto-reconnection.
 */
import { ref } from 'vue';

// ── Shared singleton state ─────────────────────────────────────────────────
const telemetryData   = ref(null);
const lastCaptureData = ref(null);
const lastEvent       = ref(null); // fires on every discrete WS event
const isConnected     = ref(false);

let ws                = null;
let reconnectTimer    = null;
let _reconnectAttempt = 0;
const RECONNECT_BASE_MS = 2000;
const RECONNECT_MAX_MS  = 30000;

function _reconnectDelay() {
  // Exponential backoff: 2s, 4s, 8s, 16s, 30s (capped)
  return Math.min(RECONNECT_BASE_MS * 2 ** _reconnectAttempt, RECONNECT_MAX_MS);
}

function _emit(type, data) {
  lastEvent.value = { type, data, ts: new Date().toISOString() };
}

export function useTelemetry() {
  const connect = () => {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;

    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const wsUrl = `${proto}://${window.location.host}/api/v1/ws/telemetry?rate_hz=2`;
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      isConnected.value = true;
      _reconnectAttempt = 0;
      _emit('ws_connected', {});
      ws.send(JSON.stringify({
        action: 'subscribe',
        topics: ['telemetry_snapshot', 'capture_event', 'lease_event', 'alarm_event']
      }));
    };

    ws.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);

        switch (payload.type) {

          case 'telemetry_snapshot':
            telemetryData.value = payload.data;
            break;

          case 'capture_event': {
            const d = payload.data ?? {};
            _emit('capture_event', d);
            if (d.event === 'capture_done') {
              lastCaptureData.value = {
                capture_id:      d.exposure_id      ?? null,
                job_id:          d.job_id           ?? null,
                exptime_s:       d.exptime_s        ?? null,
                file_path:       d.image_path       ?? null,
                sidecar_path:    d.sidecar_path     ?? null,
                image_valid:     d.image_valid      ?? null,
                image_error:     d.image_error      ?? null,
                sidecar_written: d.sidecar_written  ?? null,
                ccd_temp_c:      d.ccd_temp_c       ?? null,
                completed_at:    payload.timestamp  ?? new Date().toISOString(),
              };
            }
            break;
          }

          case 'lease_event':
            _emit('lease_event', payload.data ?? {});
            break;

          case 'alarm_event':
            _emit('alarm_event', payload.data ?? {});
            break;

          default:
            break;
        }
      } catch (err) {
        console.error('[Telemetry] Parse error:', err);
      }
    };

    ws.onclose = () => {
      isConnected.value = false;
      _emit('ws_disconnected', {});
      ws = null;
      clearTimeout(reconnectTimer);
      const delay = _reconnectDelay();
      _reconnectAttempt++;
      reconnectTimer = setTimeout(() => connect(), delay);
    };

    ws.onerror = () => {
      if (ws) ws.close();
    };
  };

  const disconnect = () => {
    clearTimeout(reconnectTimer);
    _reconnectAttempt = 0;
    if (ws) { ws.close(); ws = null; }
  };

  return { telemetryData, lastCaptureData, lastEvent, isConnected, connect, disconnect };
}