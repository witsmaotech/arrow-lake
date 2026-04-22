"""Unit tests for OpenTelemetry telemetry setup."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_setup_telemetry_disabled_is_noop() -> None:
    """When telemetry is disabled, setup_telemetry should be a no-op."""
    from arrow_lake.api.telemetry import setup_telemetry

    mock_config = MagicMock()
    mock_config.enabled = False
    mock_config.service_name = "arrow-lake"

    # Should not raise
    result = setup_telemetry(mock_config)
    assert result is None


def test_setup_telemetry_enabled_configures_tracer() -> None:
    """When telemetry is enabled, setup_telemetry should configure tracer provider."""
    from arrow_lake.api.telemetry import setup_telemetry

    mock_config = MagicMock()
    mock_config.enabled = True
    mock_config.service_name = "arrow-lake"
    mock_config.otel_endpoint = "http://localhost:4317"
    mock_config.trace_sample_rate = 1.0

    with patch("arrow_lake.api.telemetry._OTEL_AVAILABLE", True):
        with patch("arrow_lake.api.telemetry._configure_tracer") as mock_configure:
            result = setup_telemetry(mock_config)
            mock_configure.assert_called_once_with(mock_config)


def test_setup_telemetry_missing_deps_is_noop() -> None:
    """When OTel dependencies are not installed, setup_telemetry should be a no-op."""
    from arrow_lake.api.telemetry import setup_telemetry

    mock_config = MagicMock()
    mock_config.enabled = True
    mock_config.service_name = "arrow-lake"

    with patch("arrow_lake.api.telemetry._OTEL_AVAILABLE", False):
        result = setup_telemetry(mock_config)
        assert result is None


def test_otel_available_flag() -> None:
    """_OTEL_AVAILABLE should be a bool."""
    from arrow_lake.api.telemetry import _OTEL_AVAILABLE

    assert isinstance(_OTEL_AVAILABLE, bool)


def test_setup_telemetry_instrument_fastapi() -> None:
    """When enabled, FastAPIInstrumentor should be configured."""
    from arrow_lake.api.telemetry import setup_telemetry

    mock_config = MagicMock()
    mock_config.enabled = True
    mock_config.service_name = "arrow-lake"
    mock_config.otel_endpoint = "http://localhost:4317"
    mock_config.trace_sample_rate = 1.0

    with patch("arrow_lake.api.telemetry._OTEL_AVAILABLE", True):
        with patch("arrow_lake.api.telemetry._configure_tracer"):
            with patch("arrow_lake.api.telemetry._instrument_app") as mock_instrument:
                app = MagicMock()
                result = setup_telemetry(mock_config, app=app)
                mock_instrument.assert_called_once_with(app)
