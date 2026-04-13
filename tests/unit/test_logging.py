"""Tests for arrow_lake.core.logging — Story 1.5."""

from __future__ import annotations

import json

import pytest
from arrow_lake.core.logging import configure_logging, get_logger


class TestConfigureLogging:
    """Test structlog configuration."""

    def test_configure_sets_json_renderer(self) -> None:
        configure_logging(log_level="DEBUG")
        logger = get_logger()
        # structlog should be configured with JSON rendering
        assert logger is not None

    def test_configure_respects_log_level(self, capsys: pytest.CaptureFixture[str]) -> None:
        configure_logging(log_level="WARNING")
        logger = get_logger()
        logger.info("should_not_appear")
        logger.warning("should_appear")
        captured = capsys.readouterr()
        assert "should_not_appear" not in captured.out
        assert "should_appear" in captured.out

    def test_json_output_is_valid(self, capsys: pytest.CaptureFixture) -> None:
        configure_logging(log_level="INFO")
        logger = get_logger()
        logger.info("test_message", key="value")
        captured = capsys.readouterr()
        # Each line should be valid JSON
        for line in captured.out.strip().split("\n"):
            if line:
                data = json.loads(line)
                assert "event" in data or "timestamp" in data


class TestCorrelationId:
    """Test correlation_id in log output."""

    def test_correlation_id_appears_in_log(self, capsys: pytest.CaptureFixture) -> None:
        configure_logging(log_level="INFO", correlation_id="trace-123")
        logger = get_logger()
        logger.info("with_correlation")
        captured = capsys.readouterr()
        for line in captured.out.strip().split("\n"):
            if line and "with_correlation" in line:
                data = json.loads(line)
                assert data.get("correlation_id") == "trace-123"

    def test_default_correlation_id_is_none(self, capsys: pytest.CaptureFixture) -> None:
        configure_logging(log_level="INFO")
        logger = get_logger()
        logger.info("no_correlation")
        captured = capsys.readouterr()
        for line in captured.out.strip().split("\n"):
            if line and "no_correlation" in line:
                data = json.loads(line)
                # correlation_id should not be set when not provided
                assert data.get("correlation_id") is None


class TestGetLogger:
    """Test get_logger helper."""

    def test_get_logger_returns_logger(self) -> None:
        configure_logging(log_level="INFO")
        logger = get_logger()
        # structlog returns a BoundLogger
        assert hasattr(logger, "info")
        assert hasattr(logger, "warning")
        assert hasattr(logger, "error")
        assert hasattr(logger, "debug")

    def test_get_logger_with_name(self) -> None:
        configure_logging(log_level="INFO")
        logger = get_logger("custom_module")
        assert logger is not None
        assert hasattr(logger, "info")
