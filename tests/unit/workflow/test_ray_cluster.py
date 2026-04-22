"""Tests for Story 6.2 — Ray Cluster Execution."""

from __future__ import annotations

import types
from unittest.mock import MagicMock, patch

import pytest
from arrow_lake.ray_runtime.cluster import (
    RayClusterInfo,
    RayResources,
    detect_gpu,
    get_cluster_info,
    initialize_ray,
    shutdown_ray,
)


class TestRayResources:
    """Test RayResources frozen dataclass."""

    def test_defaults(self) -> None:
        r = RayResources()
        assert r.num_cpus == 2
        assert r.num_gpus == 0
        assert r.memory_mb == 4096

    def test_custom(self) -> None:
        r = RayResources(num_cpus=8, num_gpus=2, memory_mb=8192)
        assert r.num_cpus == 8
        assert r.num_gpus == 2

    def test_frozen(self) -> None:
        r = RayResources()
        with pytest.raises(AttributeError):
            r.num_cpus = 99


def _make_fake_ray(*, initialized: bool = False) -> types.ModuleType:
    """Create a fake ray module for testing."""
    fake_ray = types.ModuleType("ray")
    fake_ray.is_initialized = MagicMock(return_value=initialized)
    fake_ray.cluster_resources = MagicMock(return_value={"CPU": 8, "GPU": 2, "memory": 34359738368})
    fake_ray.init = MagicMock()
    fake_ray.shutdown = MagicMock()

    class _FakeRuntimeContext:
        def get_address(self) -> str:
            return "127.0.0.1:10001"

    fake_ray.get_runtime_context = MagicMock(return_value=_FakeRuntimeContext())
    return fake_ray


class TestDetectGpu:
    """Test GPU detection."""

    def test_returns_zero_when_ray_not_installed(self) -> None:
        with patch.dict("sys.modules", {"ray": None}):
            assert detect_gpu() == 0

    def test_returns_zero_when_ray_not_initialized(self) -> None:
        fake_ray = _make_fake_ray(initialized=False)
        with patch.dict("sys.modules", {"ray": fake_ray}):
            assert detect_gpu() == 0

    def test_returns_gpu_count(self) -> None:
        fake_ray = _make_fake_ray(initialized=True)
        with patch.dict("sys.modules", {"ray": fake_ray}):
            assert detect_gpu() == 2


class TestGetClusterInfo:
    """Test get_cluster_info."""

    def test_unavailable_when_ray_not_installed(self) -> None:
        with patch.dict("sys.modules", {"ray": None}):
            info = get_cluster_info()
            assert info.available is False

    def test_unavailable_when_ray_not_initialized(self) -> None:
        fake_ray = _make_fake_ray(initialized=False)
        with patch.dict("sys.modules", {"ray": fake_ray}):
            info = get_cluster_info()
            assert info.available is False

    def test_returns_cluster_info_when_initialized(self) -> None:
        fake_ray = _make_fake_ray(initialized=True)
        with patch.dict("sys.modules", {"ray": fake_ray}):
            info = get_cluster_info()
            assert info.available is True
            assert info.num_cpus == 8
            assert info.num_gpus == 2
            assert info.memory_bytes == 34359738368
            assert info.address == "127.0.0.1:10001"


class TestInitializeRay:
    """Test Ray initialization."""

    def test_returns_false_when_ray_not_installed(self) -> None:
        with patch.dict("sys.modules", {"ray": None}):
            assert initialize_ray() is False

    def test_returns_true_when_already_initialized(self) -> None:
        fake_ray = _make_fake_ray(initialized=True)
        with patch.dict("sys.modules", {"ray": fake_ray}):
            assert initialize_ray() is True
            fake_ray.init.assert_not_called()

    def test_calls_init_with_params(self) -> None:
        fake_ray = _make_fake_ray(initialized=False)
        # After init, is_initialized returns True for get_cluster_info
        fake_ray.is_initialized.side_effect = [False, True]
        with patch.dict("sys.modules", {"ray": fake_ray}):
            result = initialize_ray(num_cpus=4, num_gpus=1)
            fake_ray.init.assert_called_once_with(
                include_dashboard=False,
                num_cpus=4,
                num_gpus=1,
            )
            assert result is True

    def test_connects_to_remote(self) -> None:
        fake_ray = _make_fake_ray(initialized=False)
        fake_ray.is_initialized.side_effect = [False, True]
        with patch.dict("sys.modules", {"ray": fake_ray}):
            result = initialize_ray(address="ray://cluster:10001")
            fake_ray.init.assert_called_once_with(
                address="ray://cluster:10001",
                include_dashboard=False,
            )
            assert result is True

    def test_returns_false_on_exception(self) -> None:
        fake_ray = _make_fake_ray(initialized=False)
        fake_ray.init = MagicMock(side_effect=RuntimeError("cluster error"))
        with patch.dict("sys.modules", {"ray": fake_ray}):
            assert initialize_ray() is False


class TestShutdownRay:
    """Test Ray shutdown."""

    def test_noop_when_ray_not_installed(self) -> None:
        with patch.dict("sys.modules", {"ray": None}):
            shutdown_ray()  # Should not raise

    def test_calls_shutdown_when_initialized(self) -> None:
        fake_ray = _make_fake_ray(initialized=True)
        with patch.dict("sys.modules", {"ray": fake_ray}):
            shutdown_ray()
            fake_ray.shutdown.assert_called_once()

    def test_noop_when_ray_not_initialized(self) -> None:
        fake_ray = _make_fake_ray(initialized=False)
        with patch.dict("sys.modules", {"ray": fake_ray}):
            shutdown_ray()
            fake_ray.shutdown.assert_not_called()


class TestRayClusterInfo:
    """Test RayClusterInfo frozen dataclass."""

    def test_default_unavailable(self) -> None:
        info = RayClusterInfo(available=False)
        assert info.available is False
        assert info.num_cpus == 0
        assert info.num_gpus == 0

    def test_frozen(self) -> None:
        info = RayClusterInfo(available=True, num_cpus=8)
        with pytest.raises(AttributeError):
            info.num_cpus = 99
