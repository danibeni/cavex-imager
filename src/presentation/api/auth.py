"""Shared API key validation helper."""

from __future__ import annotations

from src.shared.config import Config


def is_valid_api_key(config: Config, api_key: str | None) -> bool:
    if not config.get("auth.enabled", False):
        return True
    if not api_key:
        return False
    for key_entry in config.get("auth.keys", []):
        if key_entry.get("key") == api_key:
            return True
    return False
