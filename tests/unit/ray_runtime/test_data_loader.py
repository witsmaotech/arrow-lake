"""Unit tests for ray_runtime.data_loader — PrefetchConfig, RemoteDataLoader."""

from __future__ import annotations

import pytest

from arrow_lake.ray_runtime.data_loader import PrefetchConfig


# ---------------------------------------------------------------------------
# PrefetchConfig
# ---------------------------------------------------------------------------


def test_prefetch_config_defaults():
    cfg = PrefetchConfig()
    assert cfg.queue_depth == 2


def test_prefetch_config_custom():
    cfg = PrefetchConfig(queue_depth=5)
    assert cfg.queue_depth == 5


def test_prefetch_config_invalid():
    with pytest.raises(ValueError, match="queue_depth must be >= 1"):
        PrefetchConfig(queue_depth=0)
    with pytest.raises(ValueError, match="queue_depth must be >= 1"):
        PrefetchConfig(queue_depth=-1)


def test_prefetch_config_queue_depth_is_readonly():
    cfg = PrefetchConfig(queue_depth=3)
    assert cfg.queue_depth == 3
