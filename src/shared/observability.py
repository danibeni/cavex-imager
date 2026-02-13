"""Prometheus metrics for service observability (MVP)."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

captures_total = Counter(
    "cavex_captures_total",
    "Total number of captures by final status.",
    ["status"],
)

captures_failed_total = Counter(
    "cavex_captures_failed_total",
    "Total number of failed captures.",
)

last_capture_duration_s = Gauge(
    "cavex_last_capture_duration_seconds",
    "Duration of the last completed capture in seconds.",
)

capture_duration_seconds = Histogram(
    "cavex_capture_duration_seconds",
    "Capture duration distribution in seconds.",
)

active_leases = Gauge(
    "cavex_active_leases",
    "Current number of active leases (0 or 1).",
)

indigo_connected = Gauge(
    "cavex_indigo_connected",
    "INDIGO server connection status (0/1).",
)

device_connected = Gauge(
    "cavex_device_connected",
    "Camera device connection status (0/1).",
)

ws_clients_connected = Gauge(
    "cavex_ws_clients_connected",
    "Number of connected WebSocket clients.",
)

camera_temperature_celsius = Gauge(
    "cavex_camera_temperature_celsius",
    "Current camera CCD temperature in Celsius.",
)

cooler_power_pct = Gauge(
    "cavex_cooler_power_pct",
    "Current cooler power percentage.",
)

disk_free_gb = Gauge(
    "cavex_disk_free_gb",
    "Free disk space in gigabytes.",
)
