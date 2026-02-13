"""Unit tests for shared configuration loading."""

from __future__ import annotations

from pathlib import Path

from pytest import MonkeyPatch

from src.shared.config import Config


def _default_config_path() -> Path:
    """Return repository default YAML configuration path."""
    return Path(__file__).resolve().parents[3] / "config" / "default.yaml"


def test_config_loads_yaml_file() -> None:
    """Test that configuration values are loaded from YAML."""
    config = Config(config_path=_default_config_path())
    assert config.get("service.name") == "cavex_imager"
    assert config.get("indigo.port") == 7624


def test_config_overrides_yaml_with_environment_variables(monkeypatch: MonkeyPatch) -> None:
    """Test that CAVEX_ environment variables override YAML values."""
    monkeypatch.setenv("CAVEX_INDIGO_HOST", "127.0.0.1")
    config = Config(config_path=_default_config_path())
    assert config.get("indigo.host") == "127.0.0.1"


def test_config_returns_default_when_key_does_not_exist() -> None:
    """Test default return when key path does not exist."""
    config = Config(config_path=_default_config_path())
    assert config.get("service.missing_value", "fallback") == "fallback"


def test_config_supports_dot_notation_for_nested_keys() -> None:
    """Test nested key access using dot notation."""
    config = Config(config_path=_default_config_path())
    assert config.get("lease.preempt_mode") == "graceful"
    assert config.get("lease.default_ttl_seconds") == 120
    assert config.get("storage.min_free_gb") == 10


def test_config_settings_property() -> None:
    """Test the settings property returns validated settings."""
    config = Config(config_path=_default_config_path())
    settings = config.settings
    assert settings.service.name == "cavex_imager"
    assert settings.indigo.reconnect.enabled is True
    assert settings.lease.preempt_mode == "graceful"
    assert settings.auth.enabled is False


def test_config_loads_without_yaml_file() -> None:
    """Test config loads defaults when YAML doesn't exist."""
    config = Config(config_path="/nonexistent/path.yaml")
    assert config.get("service.name") == "cavex_imager"
