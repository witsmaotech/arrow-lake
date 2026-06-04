"""Targeted tests for Lake lifecycle — context manager, shutdown, component cleanup."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from arrow_lake import Lake
from arrow_lake.config import ArrowLakeConfig, StorageBackend, StorageConfig


def _lake(tmp_path: Path) -> Lake:
    cfg = ArrowLakeConfig()
    cfg.storage = StorageConfig(base_uri=str(tmp_path / "data"), backend=StorageBackend.LOCAL)
    return Lake(base_uri=str(tmp_path / "data"), config=cfg)


class TestLakeContextManager:
    def test_enter_returns_lake(self, tmp_path: Path) -> None:
        lake = _lake(tmp_path)
        with lake as l:
            assert l is lake

    def test_exit_calls_shutdown(self, tmp_path: Path) -> None:
        lake = _lake(tmp_path)
        with lake:
            pass
        assert lake._shutdown is True


class TestLakeShutdown:
    def test_shutdown_clears_components(self, tmp_path: Path) -> None:
        lake = _lake(tmp_path)
        lake._components["test"] = MagicMock()
        lake.shutdown()
        assert lake._shutdown is True
        assert len(lake._components) == 0

    def test_shutdown_with_close_method(self, tmp_path: Path) -> None:
        lake = _lake(tmp_path)
        mock_component = MagicMock(spec=["close"])
        mock_component.close = MagicMock()
        lake._components["svc"] = mock_component
        lake.shutdown()
        mock_component.close.assert_called_once()

    def test_shutdown_with_shutdown_method(self, tmp_path: Path) -> None:
        lake = _lake(tmp_path)
        mock_component = MagicMock()
        mock_component.shutdown = MagicMock()
        lake._components["svc"] = mock_component
        lake.shutdown()
        mock_component.shutdown.assert_called_once()

    def test_shutdown_handles_component_error(self, tmp_path: Path) -> None:
        lake = _lake(tmp_path)
        mock_component = MagicMock()
        mock_component.close.side_effect = RuntimeError("boom")
        lake._components["svc"] = mock_component
        # Should not raise
        lake.shutdown()
        assert lake._shutdown is True

    def test_shutdown_idempotent(self, tmp_path: Path) -> None:
        lake = _lake(tmp_path)
        lake.shutdown()
        lake.shutdown()  # second call should be no-op
        assert lake._shutdown is True
