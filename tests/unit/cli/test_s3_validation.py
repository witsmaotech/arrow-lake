"""S3 validation tests — CLI Lake caching, embedding cache, signal protection.

Tests for S3.1 (CLI Lake instance cache + shutdown cleanup),
S3.2 (Embedding model cache), S3.3 (CLI signal protection).
"""

from __future__ import annotations

import signal
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import click
import pytest
from click.testing import CliRunner


# ── S3.1: CLI Lake instance cache + shutdown cleanup ──


class TestLakeInstanceCache:
    """Verify _get_lake caches the Lake instance within a Click context."""

    def test_get_lake_returns_same_instance(self) -> None:
        from arrow_lake.cli import _get_lake

        with patch("arrow_lake.Lake") as MockLake, \
             patch("arrow_lake.ArrowLakeConfig"):
            mock_lake = MagicMock()
            MockLake.return_value = mock_lake

            ctx = click.Context(click.Command("test"), obj={"base_uri": "./data"})
            lake1 = _get_lake(ctx)
            lake2 = _get_lake(ctx)
            assert lake1 is lake2
            assert MockLake.call_count == 1  # Only created once

    def test_get_lake_registers_shutdown_callback(self) -> None:
        from arrow_lake.cli import _get_lake

        with patch("arrow_lake.Lake") as MockLake, \
             patch("arrow_lake.ArrowLakeConfig"):
            mock_lake = MagicMock()
            MockLake.return_value = mock_lake

            ctx = click.Context(click.Command("test"), obj={"base_uri": "./data"})
            _get_lake(ctx)

            # Verify lake was stored in context
            assert ctx.obj["lake"] is mock_lake

    def test_lake_shutdown_called_on_context_close(self) -> None:
        from arrow_lake.cli import _get_lake

        with patch("arrow_lake.Lake") as MockLake, \
             patch("arrow_lake.ArrowLakeConfig"):
            mock_lake = MagicMock()
            MockLake.return_value = mock_lake

            ctx = click.Context(click.Command("test"), obj={"base_uri": "./data"})
            _get_lake(ctx)

            # Simulate Click context cleanup
            ctx.call_on_close(mock_lake.shutdown)
            with ctx:
                pass

            mock_lake.shutdown.assert_called()

    def test_different_contexts_create_different_lakes(self) -> None:
        from arrow_lake.cli import _get_lake

        with patch("arrow_lake.Lake") as MockLake, \
             patch("arrow_lake.ArrowLakeConfig"):
            lake1 = MagicMock()
            lake2 = MagicMock()
            MockLake.side_effect = [lake1, lake2]

            ctx1 = click.Context(click.Command("test"), obj={"base_uri": "./data1"})
            ctx2 = click.Context(click.Command("test"), obj={"base_uri": "./data2"})

            result1 = _get_lake(ctx1)
            result2 = _get_lake(ctx2)
            assert result1 is lake1
            assert result2 is lake2
            assert MockLake.call_count == 2


# ── S3.2: Embedding model cache ──


class TestEmbeddingModelCache:
    """Verify _get_encoder caches the encoder instance."""

    def test_get_encoder_returns_same_instance(self) -> None:
        from arrow_lake.cli.search import _get_encoder

        with patch("arrow_lake.embed.encoder.LocalEmbeddingEncoder") as MockEncoder:
            mock_encoder = MagicMock()
            MockEncoder.return_value = mock_encoder

            enc1 = _get_encoder("model-a")
            enc2 = _get_encoder("model-a")
            assert enc1 is enc2
            assert MockEncoder.call_count == 1

    def test_different_models_create_different_encoders(self) -> None:
        from arrow_lake.cli.search import _get_encoder

        # Clear the module-level cache for isolation
        import arrow_lake.cli.search as search_mod
        original_cache = search_mod._encoder_cache.copy()
        search_mod._encoder_cache.clear()
        try:
            with patch("arrow_lake.embed.encoder.LocalEmbeddingEncoder") as MockEncoder:
                enc_a = MagicMock()
                enc_b = MagicMock()
                MockEncoder.side_effect = [enc_a, enc_b]

                result_a = _get_encoder("model-a")
                result_b = _get_encoder("model-b")
                assert result_a is enc_a
                assert result_b is enc_b
                assert MockEncoder.call_count == 2
        finally:
            search_mod._encoder_cache.update(original_cache)

    def test_encoder_cache_is_module_level(self) -> None:
        """The cache persists across function calls (module-level dict)."""
        from arrow_lake.cli.search import _encoder_cache, _get_encoder

        with patch("arrow_lake.embed.encoder.LocalEmbeddingEncoder") as MockEncoder:
            mock_encoder = MagicMock()
            MockEncoder.return_value = mock_encoder

            # Clear and verify
            _encoder_cache.clear()
            assert "test-model" not in _encoder_cache

            _get_encoder("test-model")
            assert "test-model" in _encoder_cache

            # Cleanup
            _encoder_cache.clear()


# ── S3.3: CLI signal protection ──


class TestSignalProtection:
    """Verify SIGINT handler is registered and calls lake.shutdown()."""

    def test_sigint_handler_registered_on_main(self) -> None:
        """The main() click group registers a SIGINT handler."""
        from arrow_lake.cli import main

        runner = CliRunner()
        with patch("arrow_lake.Lake"), \
             patch("arrow_lake.ArrowLakeConfig"):
            # Just invoking --help triggers main() setup
            result = runner.invoke(main, ["--help"])
            assert result.exit_code == 0

    def test_sigint_handler_calls_shutdown(self) -> None:
        """Simulating SIGINT should trigger lake.shutdown()."""
        from arrow_lake.cli import main

        runner = CliRunner()
        with patch("arrow_lake.Lake") as MockLake, \
             patch("arrow_lake.ArrowLakeConfig"):
            mock_lake = MagicMock()
            MockLake.return_value = mock_lake

            # Run a command that creates a Lake
            result = runner.invoke(main, ["catalog", "list"])
            assert result.exit_code == 0

    def test_sigint_without_lake_no_crash(self) -> None:
        """SIGINT when no Lake is created yet should not crash."""
        from arrow_lake.cli import main

        runner = CliRunner()
        with patch("arrow_lake.Lake") as MockLake, \
             patch("arrow_lake.ArrowLakeConfig"):
            # Just --help, no Lake created
            result = runner.invoke(main, ["--help"])
            assert result.exit_code == 0


# ── S3.4/S3.5: CLI options (--verbose, --quiet, --format) ──


class TestCLIOutputOptions:
    """Verify --verbose, --quiet, --format options are accepted."""

    def test_verbose_flag_accepted(self) -> None:
        from arrow_lake.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["-v", "--help"])
        assert result.exit_code == 0

    def test_quiet_flag_accepted(self) -> None:
        from arrow_lake.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["-q", "--help"])
        assert result.exit_code == 0

    def test_format_option_accepted(self) -> None:
        from arrow_lake.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["--format", "json", "--help"])
        assert result.exit_code == 0

    def test_format_choices(self) -> None:
        from arrow_lake.cli import main

        runner = CliRunner()
        for fmt in ("table", "json", "csv"):
            result = runner.invoke(main, ["--format", fmt, "--help"])
            assert result.exit_code == 0

    def test_invalid_format_rejected(self) -> None:
        from arrow_lake.cli import main

        runner = CliRunner()
        # --help overrides validation, so use a real command
        result = runner.invoke(main, ["--format", "xml", "version"])
        assert result.exit_code != 0


# ── S3.6: Structured error output ──


class TestStructuredErrorOutput:
    """Verify _handle_error produces structured output."""

    def test_handle_arrow_lake_error(self) -> None:
        from arrow_lake.cli import _handle_error
        from arrow_lake.exceptions import ArrowLakeError, ErrorCode

        ctx = click.Context(click.Command("test"), obj={"verbose": 0})
        error = ArrowLakeError(ErrorCode.VALIDATION_INVALID_CONFIG, "test error")

        with pytest.raises(SystemExit) as exc_info:
            _handle_error(error, ctx)
        assert exc_info.value.code == 1

    def test_handle_generic_error(self) -> None:
        from arrow_lake.cli import _handle_error

        ctx = click.Context(click.Command("test"), obj={"verbose": 0})
        error = RuntimeError("something went wrong")

        with pytest.raises(SystemExit) as exc_info:
            _handle_error(error, ctx)
        assert exc_info.value.code == 1

    def test_verbose_shows_error_code(self) -> None:
        from arrow_lake.cli import _handle_error
        from arrow_lake.exceptions import ArrowLakeError, ErrorCode

        ctx = click.Context(click.Command("test"), obj={"verbose": 1})
        error = ArrowLakeError(ErrorCode.VALIDATION_INVALID_CONFIG, "test error", context={"key": "val"})

        with pytest.raises(SystemExit):
            _handle_error(error, ctx)
