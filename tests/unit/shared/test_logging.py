"""Unit tests for structured logging setup."""

from __future__ import annotations

from typing import Generator

import pytest
import structlog

from src.shared.logging import bind_correlation_id, clear_correlation_id, setup_logging


@pytest.fixture(autouse=True)
def _reset_structlog() -> Generator[None, None, None]:
    """Reset structlog state between tests."""
    structlog.reset_defaults()
    yield
    structlog.reset_defaults()


def test_setup_logging_configures_required_processors() -> None:
    """Test that setup_logging configures required structlog processors."""
    setup_logging(level="INFO", file_enabled=False)
    processors = structlog.get_config()["processors"]
    processor_names = [
        getattr(processor, "__name__", processor.__class__.__name__)
        for processor in processors
    ]

    assert "merge_contextvars" in processor_names
    assert "add_log_level" in processor_names
    assert "TimeStamper" in processor_names
    assert "JSONRenderer" in processor_names


def test_setup_logging_outputs_json(capsys: pytest.CaptureFixture[str]) -> None:
    """Test that logger output is rendered as JSON."""
    setup_logging(level="INFO", file_enabled=False)
    logger = structlog.get_logger("test-json-output")
    logger.info("hello", request_id="abc123")

    captured = capsys.readouterr()
    output = captured.out.strip()
    assert '"event": "hello"' in output
    assert '"request_id": "abc123"' in output
    assert '"service": "cavex_imager"' in output


def test_setup_logging_respects_log_level(capsys: pytest.CaptureFixture[str]) -> None:
    """Test that setup_logging applies log level filtering."""
    setup_logging(level="WARNING", file_enabled=False)
    logger = structlog.get_logger("test-log-level")
    logger.info("hidden_message")
    logger.warning("visible_message")

    captured = capsys.readouterr()
    output = captured.out
    assert "hidden_message" not in output
    assert "visible_message" in output


def test_bind_correlation_id(capsys: pytest.CaptureFixture[str]) -> None:
    """Test that bind_correlation_id adds id to log context."""
    setup_logging(level="INFO", file_enabled=False)
    bind_correlation_id("corr-123")

    logger = structlog.get_logger("test-correlation")
    logger.info("test_event")

    captured = capsys.readouterr()
    assert '"correlation_id": "corr-123"' in captured.out
    clear_correlation_id()
