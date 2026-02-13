"""Unit tests for observability metrics."""

from __future__ import annotations

import src.shared.observability as observability


def test_prometheus_metrics_are_registered() -> None:
    """Test that required Prometheus metrics are defined."""
    assert observability.captures_total._name == "cavex_captures"
    assert observability.captures_total._labelnames == ("status",)
    assert observability.captures_failed_total._name == "cavex_captures_failed"
    assert observability.last_capture_duration_s._name == "cavex_last_capture_duration_seconds"
    assert observability.capture_duration_seconds._name == "cavex_capture_duration_seconds"
    assert observability.active_leases._name == "cavex_active_leases"
    assert observability.indigo_connected._name == "cavex_indigo_connected"
    assert observability.device_connected._name == "cavex_device_connected"
    assert observability.ws_clients_connected._name == "cavex_ws_clients_connected"
    assert observability.camera_temperature_celsius._name == "cavex_camera_temperature_celsius"
    assert observability.cooler_power_pct._name == "cavex_cooler_power_pct"
    assert observability.disk_free_gb._name == "cavex_disk_free_gb"
