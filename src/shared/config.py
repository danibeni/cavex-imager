"""Application configuration loader aligned with spec YAML structure."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


# ── Section models ──────────────────────────────────────────────────

class ServiceConfig(BaseModel):
    """Service identity."""

    name: str = "cavex_imager"
    version: str = "1.0.0"
    log_level: str = "INFO"


class IndigoReconnectConfig(BaseModel):
    """INDIGO reconnection settings."""

    enabled: bool = True
    max_attempts: int = 0
    backoff_max_s: int = 30


class IndigoConfig(BaseModel):
    """INDIGO connection configuration."""

    host: str = "localhost"
    port: int = 7624
    device_name: str = "CCD Imager Simulator"
    reconnect: IndigoReconnectConfig = IndigoReconnectConfig()


class AuthKeyConfig(BaseModel):
    """Single API key entry."""

    key: str
    owner: str
    priority: int = 50


class AuthConfig(BaseModel):
    """API key authentication settings."""

    enabled: bool = False
    keys: list[AuthKeyConfig] = []


class LeaseConfig(BaseModel):
    """Lease policy configuration."""

    preempt_mode: str = "graceful"
    default_ttl_seconds: int = 120
    max_ttl_seconds: int = 600


class StorageConfig(BaseModel):
    """Storage path and space settings."""

    host_base_path: str = "/opt/cavex/data"
    container_base_path: str = "/data"
    min_free_gb: float = 10.0
    default_file_prefix: str = "cavex"
    default_naming_pattern: str = "{prefix}_{seq:04d}.fits"


class TelemetryConfig(BaseModel):
    """Telemetry WebSocket settings."""

    default_rate_hz: float = 1.0
    max_ws_clients: int = 10


class LoggingStdoutConfig(BaseModel):
    """Stdout logging settings."""

    enabled: bool = True
    format: str = "json"


class LoggingFileConfig(BaseModel):
    """File logging settings with rotation."""

    enabled: bool = True
    path: str = "/data/logs/cavex_imager.log"
    max_bytes: int = 10485760
    backup_count: int = 5
    compress: bool = True


class LoggingConfig(BaseModel):
    """Combined logging configuration."""

    stdout: LoggingStdoutConfig = LoggingStdoutConfig()
    file: LoggingFileConfig = LoggingFileConfig()


# ── Root settings ───────────────────────────────────────────────────

class AppSettings(BaseSettings):
    """Validated application settings."""

    model_config = SettingsConfigDict(extra="ignore")

    service: ServiceConfig = ServiceConfig()
    indigo: IndigoConfig = IndigoConfig()
    auth: AuthConfig = AuthConfig()
    lease: LeaseConfig = LeaseConfig()
    storage: StorageConfig = StorageConfig()
    telemetry: TelemetryConfig = TelemetryConfig()
    logging: LoggingConfig = LoggingConfig()


# ── Config loader ───────────────────────────────────────────────────

class Config:
    """Load configuration from YAML and environment variables.

    Environment overrides use the ``CAVEX_`` prefix with flattened key path.
    For example, ``CAVEX_INDIGO_HOST`` overrides ``indigo.host``.
    """

    def __init__(self, config_path: str | Path = "config/default.yaml") -> None:
        """Initialize the configuration object.

        Args:
            config_path: Path to a YAML configuration file.
        """
        self._config_path = Path(config_path)
        yaml_data = self._load_yaml(self._config_path)
        merged_data = self._apply_env_overrides(yaml_data)
        self._settings = AppSettings.model_validate(merged_data)
        self._data = self._settings.model_dump(mode="python")

    @property
    def settings(self) -> AppSettings:
        """Return the validated settings object."""
        return self._settings

    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value using dot notation.

        Args:
            key: Dot notation key path like ``indigo.host``.
            default: Value returned if key path does not exist.

        Returns:
            The resolved value or the provided default.
        """
        current: Any = self._data
        for token in key.split("."):
            if not isinstance(current, dict) or token not in current:
                return default
            current = current[token]
        return current

    @staticmethod
    def _load_yaml(config_path: Path) -> dict[str, Any]:
        """Load raw configuration from a YAML file.

        Args:
            config_path: Path to the YAML file.

        Returns:
            Parsed YAML content as a dictionary.

        Raises:
            FileNotFoundError: If the file does not exist.
            ValueError: If YAML content is not a mapping.
        """
        if not config_path.exists():
            return {}
        with config_path.open("r", encoding="utf-8") as file:
            data = yaml.safe_load(file)
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise ValueError("Configuration file must contain a YAML mapping.")
        return data

    def _apply_env_overrides(self, source: dict[str, Any]) -> dict[str, Any]:
        """Apply CAVEX_ environment variables to the YAML data."""
        merged = self._deep_copy(source)
        for env_key, path in self._build_env_mapping(source).items():
            env_value = os.getenv(env_key)
            if env_value is None:
                continue
            self._set_nested_value(merged, path, env_value)
        return merged

    @staticmethod
    def _build_env_mapping(source: dict[str, Any]) -> dict[str, tuple[str, ...]]:
        """Build environment variable to nested-path mapping."""
        env_mapping: dict[str, tuple[str, ...]] = {}

        def walk(node: Any, path: tuple[str, ...]) -> None:
            if isinstance(node, dict):
                for child_key, child_value in node.items():
                    walk(child_value, (*path, child_key))
                return
            env_name = "CAVEX_" + "_".join(path).upper()
            env_mapping[env_name] = path

        walk(source, ())
        return env_mapping

    @staticmethod
    def _set_nested_value(target: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
        """Set a value inside a nested dictionary path."""
        current: dict[str, Any] = target
        for key in path[:-1]:
            child = current.setdefault(key, {})
            if not isinstance(child, dict):
                raise ValueError(f"Cannot set nested key on non-mapping: {'.'.join(path)}")
            current = child
        current[path[-1]] = value

    @staticmethod
    def _deep_copy(data: dict[str, Any]) -> dict[str, Any]:
        """Create a deep copy for a dict tree."""
        copied: dict[str, Any] = {}
        for key, value in data.items():
            copied[key] = Config._deep_copy(value) if isinstance(value, dict) else value
        return copied
