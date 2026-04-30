"""Structured logging configuration with stdout and rotated file output."""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import structlog


def setup_logging(
    level: str = "INFO",
    file_enabled: bool = True,
    file_path: str = "/data/logs/cavex_imager.log",
    max_bytes: int = 10_485_760,
    backup_count: int = 5,
) -> None:
    """Configure stdlib and structlog logging.

    Dual output: JSON to stdout and rotated log file.

    Args:
        level: Logging level name (e.g. ``INFO``, ``DEBUG``).
        file_enabled: Whether to enable file logging.
        file_path: Path to the rotating log file.
        max_bytes: Maximum size per log file in bytes.
        backup_count: Number of rotated backup files to keep.
    """
    normalized_level = getattr(logging, level.upper(), logging.INFO)

    # Root logger setup
    root_logger = logging.getLogger()
    root_logger.setLevel(normalized_level)
    root_logger.handlers.clear()

    # Stdout handler (JSON via structlog)
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(normalized_level)
    root_logger.addHandler(stdout_handler)

    # Rotating file handler
    if file_enabled:
        log_dir = Path(file_path).parent
        log_dir.mkdir(parents=True, exist_ok=True)

        file_handler = RotatingFileHandler(
            filename=file_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(normalized_level)
        root_logger.addHandler(file_handler)

    # Structlog configuration
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            _add_service_context,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def _add_service_context(
    logger: structlog.types.WrappedLogger,
    method_name: str,
    event_dict: dict,
) -> dict:
    """Add service name to all log entries."""
    event_dict.setdefault("service", "cavex_imager")
    return event_dict


def bind_correlation_id(correlation_id: str) -> None:
    """Bind a correlation_id to the current context for log propagation.

    Args:
        correlation_id: Unique request identifier.
    """
    structlog.contextvars.bind_contextvars(correlation_id=correlation_id)


def clear_correlation_id() -> None:
    """Clear the current correlation_id from structlog context."""
    structlog.contextvars.unbind_contextvars("correlation_id")
