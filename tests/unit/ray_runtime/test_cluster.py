"""Unit tests for ray_runtime.cluster — RayResources, RayClusterInfo, detect_gpu, get_cluster_info, initialize_ray, shutdown_ray."""

from __future__ import annotations

from dataclasses import asdict
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


# ---------------------------------------------------------------------------
# RayResources
# ---------------------------------------------------------------------------


def test_ray_resources_defaults():
    r = RayResources()
    assert r.num_cpus == 2
    assert r.num_gpus == 0
    assert r.memory_mb == 4096
    assert r.object_store_memory_mb == 512


def test_ray_resources_custom():
    r = RayResources(num_cpus=8, num_gpus=2, memory_mb=16384, object_store_memory_mb=2048)
    assert r.num_cpus == 8
    assert r.num_gpus == 2


def test_ray_resources_is_frozen():
    r = RayResources()
    with pytest.raises(AttributeError):
        r.num_cpus = 4  # type: ignore[misc]


# ---------------------------------------------------------------------------
# RayClusterInfo
# ---------------------------------------------------------------------------


def test_ray_cluster_info_defaults():
    info = RayClusterInfo(available=False)
    assert info.available is False
    assert info.num_cpus == 0
    assert info.num_gpus == 0
    assert info.address == ""


def test_ray_cluster_info_fields():
    info = RayClusterInfo(available=True, num_cpus=16, num_gpus=4, memory_bytes=68719476736, address="192.168.1.1:6379")
    d = asdict(info)
    assert d["available"] is True
    assert d["num_gpus"] == 4


def test_ray_cluster_info_is_frozen():
    info = RayClusterInfo(available=False)
    with pytest.raises(AttributeError):
        info.available = True  # type: ignore[misc]


# ---------------------------------------------------------------------------
# detect_gpu
# ---------------------------------------------------------------------------


def test_detect_gpu_ray_not_installed():
    with patch.dict("sys.modules", {"ray": None}):
        assert detect_gpu() == 0


def test_detect_gpu_ray_not_initialized():
    mock_ray = MagicMock()
    mock_ray.is_initialized.return_value = False
    with patch.dict("sys.modules", {"ray": mock_ray}):
        assert detect_gpu() == 0


def test_detect_gpu_returns_count():
    mock_ray = MagicMock()
    mock_ray.is_initialized.return_value = True
    mock_ray.cluster_resources.return_value = {"CPU": 8, "GPU": 2, "memory": 1000}
    with patch.dict("sys.modules", {"ray": mock_ray}):
        assert detect_gpu() == 2


def test_detect_gpu_no_gpu_key():
    mock_ray = MagicMock()
    mock_ray.is_initialized.return_value = True
    mock_ray.cluster_resources.return_value = {"CPU": 8, "memory": 1000}
    with patch.dict("sys.modules", {"ray": mock_ray}):
        assert detect_gpu() == 0


def test_detect_gpu_runtime_error():
    mock_ray = MagicMock()
    mock_ray.is_initialized.side_effect = RuntimeError("cluster error")
    with patch.dict("sys.modules", {"ray": mock_ray}):
        assert detect_gpu() == 0


# ---------------------------------------------------------------------------
# get_cluster_info
# ---------------------------------------------------------------------------


def test_get_cluster_info_ray_not_installed():
    with patch.dict("sys.modules", {"ray": None}):
        info = get_cluster_info()
        assert info.available is False


def test_get_cluster_info_ray_not_initialized():
    mock_ray = MagicMock()
    mock_ray.is_initialized.return_value = False
    with patch.dict("sys.modules", {"ray": mock_ray}):
        info = get_cluster_info()
        assert info.available is False


def test_get_cluster_info_success():
    mock_ray = MagicMock()
    mock_ray.is_initialized.return_value = True
    mock_ray.cluster_resources.return_value = {"CPU": 8, "GPU": 2, "memory": 68719476736}
    mock_ctx = MagicMock()
    mock_ctx.get_address.return_value = "192.168.1.1:6379"
    mock_ray.get_runtime_context.return_value = mock_ctx
    with patch.dict("sys.modules", {"ray": mock_ray}):
        info = get_cluster_info()
    assert info.available is True
    assert info.num_cpus == 8
    assert info.num_gpus == 2
    assert info.address == "192.168.1.1:6379"


def test_get_cluster_info_address_error():
    mock_ray = MagicMock()
    mock_ray.is_initialized.return_value = True
    mock_ray.cluster_resources.return_value = {"CPU": 4}
    mock_ray.get_runtime_context.side_effect = RuntimeError("no runtime context")
    with patch.dict("sys.modules", {"ray": mock_ray}):
        info = get_cluster_info()
    assert info.available is True
    assert info.address == ""


# ---------------------------------------------------------------------------
# initialize_ray
# ---------------------------------------------------------------------------


def test_initialize_ray_not_installed():
    with patch.dict("sys.modules", {"ray": None}):
        assert initialize_ray() is False


def test_initialize_ray_already_initialized():
    mock_ray = MagicMock()
    mock_ray.is_initialized.return_value = True
    with patch.dict("sys.modules", {"ray": mock_ray}):
        assert initialize_ray() is True
        mock_ray.init.assert_not_called()


def test_initialize_ray_local():
    mock_ray = MagicMock()
    mock_ray.is_initialized.return_value = False
    with patch.dict("sys.modules", {"ray": mock_ray}):
        with patch("arrow_lake.ray_runtime.cluster.get_cluster_info", return_value=RayClusterInfo(available=True, num_cpus=4)):
            result = initialize_ray(num_cpus=4, num_gpus=1, object_store_memory=1024)
    assert result is True
    mock_ray.init.assert_called_once_with(
        include_dashboard=False,
        num_cpus=4,
        num_gpus=1,
        object_store_memory=1024,
    )


def test_initialize_ray_connect_to_cluster():
    mock_ray = MagicMock()
    mock_ray.is_initialized.return_value = False
    with patch.dict("sys.modules", {"ray": mock_ray}):
        with patch("arrow_lake.ray_runtime.cluster.get_cluster_info", return_value=RayClusterInfo(available=True)):
            result = initialize_ray(address="ray://10.0.0.1:6379")
    assert result is True
    mock_ray.init.assert_called_once_with(
        include_dashboard=False,
        address="ray://10.0.0.1:6379",
    )


def test_initialize_ray_runtime_error():
    mock_ray = MagicMock()
    mock_ray.is_initialized.return_value = False
    mock_ray.init.side_effect = RuntimeError("init failed")
    with patch.dict("sys.modules", {"ray": mock_ray}):
        assert initialize_ray() is False


# ---------------------------------------------------------------------------
# shutdown_ray
# ---------------------------------------------------------------------------


def test_shutdown_ray_not_installed():
    with patch.dict("sys.modules", {"ray": None}):
        shutdown_ray()  # should not raise


def test_shutdown_ray_not_initialized():
    mock_ray = MagicMock()
    mock_ray.is_initialized.return_value = False
    with patch.dict("sys.modules", {"ray": mock_ray}):
        shutdown_ray()
        mock_ray.shutdown.assert_not_called()


def test_shutdown_ray_success():
    mock_ray = MagicMock()
    mock_ray.is_initialized.return_value = True
    with patch.dict("sys.modules", {"ray": mock_ray}):
        shutdown_ray()
        mock_ray.shutdown.assert_called_once()
