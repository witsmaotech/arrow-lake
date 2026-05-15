"""Tests for arrow_lake.api.telemetry.

Covers all branches in setup_telemetry, _configure_tracer, _instrument_app,
get_tracer, and the _NoOpTracer / _NoOpSpan fallback classes.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides: object) -> SimpleNamespace:
    """Build a config object with sensible defaults for telemetry tests."""
    defaults: dict[str, object] = {
        "enabled": True,
        "service_name": "test-service",
        "otel_endpoint": "http://localhost:4317",
        "trace_sample_rate": 0.1,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# setup_telemetry — disabled config
# ---------------------------------------------------------------------------


class TestSetupTelemetryDisabled:
    """Branch: config.enabled is False — should return immediately."""

    def test_returns_early_when_disabled(self) -> None:
        from arrow_lake.api.telemetry import setup_telemetry

        config = _make_config(enabled=False)

        with patch("arrow_lake.api.telemetry._configure_tracer") as mock_cfg:
            setup_telemetry(config)
            mock_cfg.assert_not_called()

    def test_returns_early_when_disabled_even_with_app(self) -> None:
        from arrow_lake.api.telemetry import setup_telemetry

        config = _make_config(enabled=False)
        app = MagicMock()

        with patch("arrow_lake.api.telemetry._configure_tracer") as mock_cfg, \
             patch("arrow_lake.api.telemetry._instrument_app") as mock_inst:
            setup_telemetry(config, app=app)
            mock_cfg.assert_not_called()
            mock_inst.assert_not_called()


# ---------------------------------------------------------------------------
# setup_telemetry — OTel not available
# ---------------------------------------------------------------------------


class TestSetupTelemetryOtelUnavailable:
    """Branch: _OTEL_AVAILABLE is False — should log and return."""

    def test_logs_and_returns_when_otel_missing(self) -> None:
        from arrow_lake.api.telemetry import setup_telemetry

        config = _make_config(enabled=True)

        with patch("arrow_lake.api.telemetry._OTEL_AVAILABLE", False), \
             patch("arrow_lake.api.telemetry._configure_tracer") as mock_cfg, \
             patch("arrow_lake.api.telemetry.logger") as mock_logger:
            setup_telemetry(config)
            mock_cfg.assert_not_called()
            mock_logger.info.assert_called_once()
            msg = mock_logger.info.call_args[0][0]
            assert "dependencies not installed" in msg

    def test_otel_unavailable_skips_instrumentation(self) -> None:
        from arrow_lake.api.telemetry import setup_telemetry

        config = _make_config(enabled=True)
        app = MagicMock()

        with patch("arrow_lake.api.telemetry._OTEL_AVAILABLE", False), \
             patch("arrow_lake.api.telemetry._configure_tracer") as mock_cfg, \
             patch("arrow_lake.api.telemetry._instrument_app") as mock_inst:
            setup_telemetry(config, app=app)
            mock_cfg.assert_not_called()
            mock_inst.assert_not_called()


# ---------------------------------------------------------------------------
# setup_telemetry — full OTel path, no app
# ---------------------------------------------------------------------------


class TestSetupTelemetryFullNoApp:
    """Branch: enabled=True, OTel available, no app passed."""

    def test_configures_tracer_without_instrumenting(self) -> None:
        from arrow_lake.api.telemetry import setup_telemetry

        config = _make_config(enabled=True)

        with patch("arrow_lake.api.telemetry._OTEL_AVAILABLE", True), \
             patch("arrow_lake.api.telemetry._configure_tracer") as mock_cfg, \
             patch("arrow_lake.api.telemetry._instrument_app") as mock_inst:
            setup_telemetry(config)
            mock_cfg.assert_called_once_with(config)
            mock_inst.assert_not_called()


# ---------------------------------------------------------------------------
# setup_telemetry — full OTel path, with app
# ---------------------------------------------------------------------------


class TestSetupTelemetryFullWithApp:
    """Branch: enabled=True, OTel available, app passed."""

    def test_configures_and_instruments(self) -> None:
        from arrow_lake.api.telemetry import setup_telemetry

        config = _make_config(enabled=True)
        app = MagicMock()

        with patch("arrow_lake.api.telemetry._OTEL_AVAILABLE", True), \
             patch("arrow_lake.api.telemetry._configure_tracer") as mock_cfg, \
             patch("arrow_lake.api.telemetry._instrument_app") as mock_inst:
            setup_telemetry(config, app=app)
            mock_cfg.assert_called_once_with(config)
            mock_inst.assert_called_once_with(app)


# ---------------------------------------------------------------------------
# _configure_tracer — OTel unavailable
# ---------------------------------------------------------------------------


class TestConfigureTracerOtelUnavailable:
    """Branch: _OTEL_AVAILABLE is False — returns early."""

    def test_noop_when_otel_unavailable(self) -> None:
        from arrow_lake.api.telemetry import _configure_tracer

        config = _make_config()

        with patch("arrow_lake.api.telemetry._OTEL_AVAILABLE", False):
            _configure_tracer(config)  # should not raise


# ---------------------------------------------------------------------------
# _configure_tracer — OTel available
# ---------------------------------------------------------------------------


class TestConfigureTracerOtelAvailable:
    """Branch: _OTEL_AVAILABLE is True — full OTLP setup."""

    @patch("arrow_lake.api.telemetry._OTEL_AVAILABLE", True)
    def test_sets_up_provider_and_exporter(self) -> None:
        from arrow_lake.api.telemetry import _configure_tracer

        config = _make_config(
            service_name="my-svc",
            otel_endpoint="http://otlp:4317",
            trace_sample_rate=0.5,
        )

        MagicMock()
        mock_provider = MagicMock()
        mock_exporter = MagicMock()
        mock_batch = MagicMock()
        mock_sampler = MagicMock()

        with patch("arrow_lake.api.telemetry.Resource") as mock_res_cls, \
             patch("arrow_lake.api.telemetry.TracerProvider", return_value=mock_provider) as mock_prov_cls, \
             patch("arrow_lake.api.telemetry.TraceIdRatioBased", return_value=mock_sampler) as mock_sampler_cls, \
             patch("arrow_lake.api.telemetry.trace") as mock_trace, \
             patch("arrow_lake.api.telemetry.BatchSpanProcessor", return_value=mock_batch) as mock_batch_cls, \
             patch("arrow_lake.api.telemetry.logger"):
            with patch.dict("sys.modules", {
                "opentelemetry.exporter.otlp.proto.grpc.trace_exporter": MagicMock(
                    OTLPSpanExporter=MagicMock(return_value=mock_exporter),
                ),
            }):
                _configure_tracer(config)

        mock_res_cls.create.assert_called_once()
        mock_sampler_cls.assert_called_once_with(0.5)
        mock_prov_cls.assert_called_once()
        mock_batch_cls.assert_called_once_with(mock_exporter)
        mock_provider.add_span_processor.assert_called_once_with(mock_batch)
        mock_trace.set_tracer_provider.assert_called_once_with(mock_provider)


# ---------------------------------------------------------------------------
# _instrument_app — OTel unavailable
# ---------------------------------------------------------------------------


class TestInstrumentAppOtelUnavailable:
    """Branch: _OTEL_AVAILABLE is False — returns early."""

    def test_noop_when_otel_unavailable(self) -> None:
        from arrow_lake.api.telemetry import _instrument_app

        app = MagicMock()

        with patch("arrow_lake.api.telemetry._OTEL_AVAILABLE", False):
            _instrument_app(app)  # should not raise


# ---------------------------------------------------------------------------
# _instrument_app — ImportError for FastAPI instrumentation
# ---------------------------------------------------------------------------


class TestInstrumentAppImportError:
    """Branch: FastAPIInstrumentor import fails — logs warning."""

    def test_warns_on_import_error(self) -> None:
        """When FastAPIInstrumentor cannot be imported, _instrument_app logs a warning."""
        import builtins

        from arrow_lake.api.telemetry import _instrument_app

        app = MagicMock()
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if "opentelemetry.instrumentation" in name:
                raise ImportError("no fastapi instr")
            return real_import(name, *args, **kwargs)

        with patch("arrow_lake.api.telemetry._OTEL_AVAILABLE", True), \
             patch("arrow_lake.api.telemetry.logger") as mock_logger:
            builtins.__import__ = fake_import
            try:
                _instrument_app(app)
            finally:
                builtins.__import__ = real_import

        mock_logger.warning.assert_called_once()
        call_args = mock_logger.warning.call_args[0]
        assert "Failed to instrument FastAPI" in call_args[0]


# ---------------------------------------------------------------------------
# _instrument_app — successful instrumentation
# ---------------------------------------------------------------------------


class TestInstrumentAppSuccess:
    """Branch: FastAPIInstrumentor available and works."""

    def test_instruments_app(self) -> None:
        from arrow_lake.api.telemetry import _instrument_app

        app = MagicMock()
        mock_instrumentor = MagicMock()

        with patch("arrow_lake.api.telemetry._OTEL_AVAILABLE", True), \
             patch("arrow_lake.api.telemetry.logger"):
            with patch.dict("sys.modules", {
                "opentelemetry.instrumentation.fastapi": MagicMock(
                    FastAPIInstrumentor=MagicMock(return_value=mock_instrumentor),
                ),
            }):
                _instrument_app(app)

            mock_instrumentor.instrument_app.assert_called_once_with(app)


# ---------------------------------------------------------------------------
# get_tracer — OTel unavailable
# ---------------------------------------------------------------------------


class TestGetTracerOtelUnavailable:
    """Branch: _OTEL_AVAILABLE is False — returns _NoOpTracer."""

    def test_returns_noop_tracer(self) -> None:
        from arrow_lake.api.telemetry import _NoOpTracer, get_tracer

        with patch("arrow_lake.api.telemetry._OTEL_AVAILABLE", False):
            tracer = get_tracer("test-lib")
            assert isinstance(tracer, _NoOpTracer)

    def test_default_name(self) -> None:
        from arrow_lake.api.telemetry import _NoOpTracer, get_tracer

        with patch("arrow_lake.api.telemetry._OTEL_AVAILABLE", False):
            tracer = get_tracer()
            assert isinstance(tracer, _NoOpTracer)


# ---------------------------------------------------------------------------
# get_tracer — OTel available
# ---------------------------------------------------------------------------


class TestGetTracerOtelAvailable:
    """Branch: _OTEL_AVAILABLE is True — returns real tracer."""

    def test_delegates_to_otel(self) -> None:
        """get_tracer delegates to opentelemetry.trace.get_tracer when OTel is available."""
        mock_tracer = MagicMock()
        mock_trace = MagicMock()
        mock_trace.get_tracer.return_value = mock_tracer

        # Place a mock opentelemetry module that has our mock trace attribute
        import sys
        real_opentelemetry = sys.modules.get("opentelemetry")
        mock_otel_mod = MagicMock()
        mock_otel_mod.trace = mock_trace
        sys.modules["opentelemetry"] = mock_otel_mod

        try:
            # Reload the module to pick up the mocked import
            import importlib

            from arrow_lake.api import telemetry
            importlib.reload(telemetry)
            result = telemetry.get_tracer("my-lib")
            mock_trace.get_tracer.assert_called_once_with("my-lib")
            assert result is mock_tracer
        finally:
            # Restore original
            if real_opentelemetry is not None:
                sys.modules["opentelemetry"] = real_opentelemetry
            importlib.reload(telemetry)


# ---------------------------------------------------------------------------
# _NoOpTracer
# ---------------------------------------------------------------------------


class TestNoOpTracer:
    """Verify _NoOpTracer.start_as_current_span returns a working _NoOpSpan."""

    def test_start_span_returns_span(self) -> None:
        from arrow_lake.api.telemetry import _NoOpTracer

        tracer = _NoOpTracer()
        span = tracer.start_as_current_span("test-op", extra_kwarg="val")
        assert span is not None

    def test_start_span_with_kwargs(self) -> None:
        from arrow_lake.api.telemetry import _NoOpTracer

        tracer = _NoOpTracer()
        span = tracer.start_as_current_span("op", attributes={"key": "val"})
        # Should not raise and should return a span
        assert span is not None


# ---------------------------------------------------------------------------
# _NoOpSpan
# ---------------------------------------------------------------------------


class TestNoOpSpan:
    """Verify all _NoOpSpan methods are safe no-ops."""

    def _make_span(self):
        from arrow_lake.api.telemetry import _NoOpTracer

        tracer = _NoOpTracer()
        return tracer.start_as_current_span("test")

    def test_context_manager_enter(self) -> None:
        span = self._make_span()
        result = span.__enter__()
        assert result is span

    def test_context_manager_exit(self) -> None:
        span = self._make_span()
        span.__enter__()
        # None of these should raise
        span.__exit__(None, None, None)

    def test_set_attribute(self) -> None:
        span = self._make_span()
        span.set_attribute("key", "value")  # no-op

    def test_add_event(self) -> None:
        span = self._make_span()
        span.add_event("event_name", extra="data")  # no-op

    def test_end(self) -> None:
        span = self._make_span()
        span.end()  # no-op

    def test_record_exception(self) -> None:
        span = self._make_span()
        exc = ValueError("test")
        span.record_exception(exc)  # no-op

    def test_set_status(self) -> None:
        span = self._make_span()
        span.set_status(MagicMock())  # no-op

    def test_is_recording_false(self) -> None:
        span = self._make_span()
        assert span.is_recording is False

    def test_with_statement(self) -> None:
        from arrow_lake.api.telemetry import _NoOpTracer

        tracer = _NoOpTracer()
        with tracer.start_as_current_span("outer") as outer:
            assert outer.is_recording is False
            outer.set_attribute("a", 1)
            outer.add_event("ev")
            with tracer.start_as_current_span("inner") as inner:
                assert inner.is_recording is False
                inner.set_status(MagicMock())
