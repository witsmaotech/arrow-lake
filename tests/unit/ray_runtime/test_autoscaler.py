"""Unit tests for ray_runtime.autoscaler — ScalingDirection, ScalingEvent, GPUAutoscaler."""

from __future__ import annotations

from dataclasses import asdict

import pytest

from arrow_lake.config import AutoscaleConfig
from arrow_lake.ray_runtime.autoscaler import ScalingDirection, ScalingEvent, GPUAutoscaler


# ---------------------------------------------------------------------------
# ScalingDirection
# ---------------------------------------------------------------------------


def test_scaling_direction_values():
    assert ScalingDirection.UP == "up"
    assert ScalingDirection.DOWN == "down"
    assert ScalingDirection.NONE == "none"


# ---------------------------------------------------------------------------
# ScalingEvent
# ---------------------------------------------------------------------------


def test_scaling_event_fields():
    event = ScalingEvent(
        direction=ScalingDirection.UP,
        previous_workers=0,
        target_workers=2,
        reason="queue depth exceeded threshold",
        timestamp=1000.0,
    )
    assert event.direction == ScalingDirection.UP
    assert event.previous_workers == 0
    assert event.target_workers == 2
    assert event.reason == "queue depth exceeded threshold"
    d = asdict(event)
    assert d["direction"] == "up"


def test_scaling_event_is_frozen():
    event = ScalingEvent(
        direction=ScalingDirection.NONE,
        previous_workers=2,
        target_workers=2,
        reason="stable",
        timestamp=0.0,
    )
    with pytest.raises(AttributeError):
        event.target_workers = 5  # type: ignore[misc]


# ---------------------------------------------------------------------------
# GPUAutoscaler
# ---------------------------------------------------------------------------


def test_autoscaler_creation():
    config = AutoscaleConfig()
    scaler = GPUAutoscaler(config)
    assert scaler is not None
    assert scaler.current_workers == config.min_workers


def test_autoscaler_disabled_returns_none():
    config = AutoscaleConfig(enabled=False)
    scaler = GPUAutoscaler(config)
    event = scaler.evaluate(queue_depth=100)
    assert event.direction == ScalingDirection.NONE


def test_autoscaler_scale_up_on_queue_depth():
    config = AutoscaleConfig(enabled=True, min_workers=1, max_workers=10)
    scaler = GPUAutoscaler(config)
    event = scaler.evaluate(
        current_tasks=0,
        available_gpus=0,
        queue_depth=10,
    )
    assert event.direction == ScalingDirection.UP
    assert event.target_workers > scaler.current_workers


def test_autoscaler_idle_no_scale_down_without_timeout():
    config = AutoscaleConfig(enabled=True, min_workers=1, max_workers=5, idle_timeout_seconds=300)
    scaler = GPUAutoscaler(config)
    event = scaler.evaluate(current_tasks=0, queue_depth=0)
    assert event.direction == ScalingDirection.NONE
