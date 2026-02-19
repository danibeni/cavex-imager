"""API error payload helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).isoformat()


def error_payload(
    code: str,
    message: str,
    correlation_id: str | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build spec-compliant API error envelope."""
    return {
        "error": {
            "code": code,
            "message": message,
            "context": context or {},
            "correlation_id": correlation_id,
            "timestamp": utc_now_iso(),
        }
    }
