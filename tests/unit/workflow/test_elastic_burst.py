"""Tests for Story 7.5 — Elastic GPU Burst Scaling."""

from __future__ import annotations

import json
import time

import pytest
from arrow_lake.config import AutoscaleConfig
from arrow_lake.ray_runtime.autoscaler import GPUAutoscaler, ScalingDirection, ScalingEvent


class TestGPUAutoscalerInit:
    """Test GPUAutoscaler initialization."""

    def test_default_config(self) -> None:
        autoscaler = GPUAutoscaler()
        assert autoscaler.config.enabled is False
        assert autoscaler.config.max_workers == 8
        assert autoscaler.config.min_workers == 0
        assert autoscaler.current_workers == 0

    def test_custom_config(self) -> None:
        cfg = AutoscaleConfig(
            enabled=True,
            min_workers=1,
            max_workers=16,
            spot_preference=0.5,
            gpu_increment=1.0,
        )
        autoscaler = GPUAutoscaler(config=cfg)
        assert autoscaler.config.enabled is True
        assert autoscaler.config.max_workers == 16
        assert autoscaler.current_workers == 1

    def test_disabled_by_default(self) -> None:
        autoscaler = GPUAutoscaler()
        event = autoscaler.evaluate(current_tasks=100, queue_depth=50)
        assert event.direction == ScalingDirection.NONE
        assert "disabled" in event.reason


class TestEvaluateScaling:
    """Test scaling evaluation logic."""

    def _make_enabled_config(self, **overrides: object) -> AutoscaleConfig:
        defaults = {
            "enabled": True,
            "min_workers": 0,
            "max_workers": 8,
            "idle_timeout_seconds": 600,
            "gpu_increment": 1.0,
            "spot_preference": 0.8,
        }
        defaults.update(overrides)
        return AutoscaleConfig(**defaults)

    def test_no_scaling_when_idle_and_no_queue(self) -> None:
        autoscaler = GPUAutoscaler(config=self._make_enabled_config())
        event = autoscaler.evaluate(current_tasks=0, queue_depth=0)
        assert event.direction == ScalingDirection.NONE

    def test_scale_up_when_queue_depth_exceeds_capacity(self) -> None:
        autoscaler = GPUAutoscaler(config=self._make_enabled_config())
        event = autoscaler.evaluate(current_tasks=2, available_gpus=1, queue_depth=5)
        assert event.direction == ScalingDirection.UP
        assert event.target_workers > 0

    def test_scale_down_when_idle_timeout_reached(self) -> None:
        autoscaler = GPUAutoscaler(config=self._make_enabled_config(idle_timeout_seconds=60))
        # Simulate idle: set last_activity far in the past
        autoscaler._last_activity_time = time.time() - 120  # 2 minutes ago
        event = autoscaler.evaluate(current_tasks=0, queue_depth=0)
        assert event.direction == ScalingDirection.DOWN
        assert event.target_workers == 0

    def test_no_change_when_at_max_workers(self) -> None:
        autoscaler = GPUAutoscaler(config=self._make_enabled_config(max_workers=2))
        autoscaler._current_workers = 2
        event = autoscaler.evaluate(current_tasks=5, available_gpus=0, queue_depth=100)
        assert event.target_workers <= 2

    def test_no_change_when_at_min_workers_and_no_demand(self) -> None:
        autoscaler = GPUAutoscaler(config=self._make_enabled_config(min_workers=1))
        autoscaler._current_workers = 1
        event = autoscaler.evaluate(current_tasks=0, queue_depth=0)
        assert event.direction == ScalingDirection.NONE

    def test_respects_max_workers_limit(self) -> None:
        autoscaler = GPUAutoscaler(config=self._make_enabled_config(max_workers=4))
        event = autoscaler.evaluate(current_tasks=0, available_gpus=0, queue_depth=100)
        assert event.target_workers <= 4

    def test_activity_resets_idle_timer(self) -> None:
        autoscaler = GPUAutoscaler(config=self._make_enabled_config(idle_timeout_seconds=60))
        # Set idle time in the past, then record activity
        autoscaler._last_activity_time = time.time() - 120
        autoscaler.record_activity()
        event = autoscaler.evaluate(current_tasks=1, queue_depth=0)
        assert event.direction == ScalingDirection.NONE


class TestFractionalGPU:
    """Test fractional GPU allocation."""

    def test_half_gpu_increment(self) -> None:
        cfg = AutoscaleConfig(
            enabled=True,
            max_workers=8,
            gpu_increment=0.5,
        )
        autoscaler = GPUAutoscaler(config=cfg)
        # 3 tasks queued, 0 available GPUs → need 3 GPUs → 6 half-GPU workers
        event = autoscaler.evaluate(current_tasks=0, available_gpus=0, queue_depth=3)
        assert event.direction == ScalingDirection.UP
        assert event.metadata["gpu_per_worker"] == 0.5

    def test_full_gpu_increment(self) -> None:
        cfg = AutoscaleConfig(
            enabled=True,
            max_workers=8,
            gpu_increment=1.0,
        )
        autoscaler = GPUAutoscaler(config=cfg)
        event = autoscaler.evaluate(current_tasks=0, available_gpus=0, queue_depth=3)
        assert event.metadata["gpu_per_worker"] == 1.0


class TestSpotPreference:
    """Test spot vs on-demand instance selection."""

    def test_always_spot_when_preference_is_1(self) -> None:
        cfg = AutoscaleConfig(enabled=True, spot_preference=1.0)
        autoscaler = GPUAutoscaler(config=cfg)
        assert autoscaler._choose_instance_type() == "spot"

    def test_always_ondemand_when_preference_is_0(self) -> None:
        cfg = AutoscaleConfig(enabled=True, spot_preference=0.0)
        autoscaler = GPUAutoscaler(config=cfg)
        assert autoscaler._choose_instance_type() == "on-demand"

    def test_mixed_selection_in_between(self) -> None:
        # With 0.5 preference, should return one of "spot" or "on-demand"
        cfg = AutoscaleConfig(enabled=True, spot_preference=0.5)
        autoscaler = GPUAutoscaler(config=cfg)
        results = {autoscaler._choose_instance_type() for _ in range(100)}
        assert "spot" in results
        assert "on-demand" in results


class TestScalingEvent:
    """Test ScalingEvent dataclass."""

    def test_to_json_is_valid_json(self) -> None:
        event = ScalingEvent(
            direction=ScalingDirection.UP,
            previous_workers=0,
            target_workers=4,
            reason="queue_depth=100",
        )
        json_str = event.to_json()
        parsed = json.loads(json_str)
        assert parsed["direction"] == "up"
        assert parsed["target_workers"] == 4

    def test_to_json_contains_all_fields(self) -> None:
        event = ScalingEvent(
            direction=ScalingDirection.DOWN,
            previous_workers=8,
            target_workers=0,
            reason="idle timeout",
            metadata={"instance_type": "spot"},
        )
        parsed = json.loads(event.to_json())
        assert "event_type" in parsed
        assert "direction" in parsed
        assert "previous_workers" in parsed
        assert "target_workers" in parsed
        assert "reason" in parsed
        assert "timestamp" in parsed
        assert "metadata" in parsed

    def test_frozen_dataclass(self) -> None:
        event = ScalingEvent(
            direction=ScalingDirection.NONE,
            previous_workers=2,
            target_workers=2,
            reason="test",
        )
        with pytest.raises(AttributeError):
            event.target_workers = 99  # type: ignore[misc]

    def test_default_timestamp(self) -> None:
        before = time.time()
        event = ScalingEvent(
            direction=ScalingDirection.NONE,
            previous_workers=0,
            target_workers=0,
            reason="test",
        )
        assert event.timestamp >= before
        assert event.timestamp <= time.time()


class TestIdleTimeout:
    """Test idle timeout behavior."""

    def test_scale_to_zero_after_idle(self) -> None:
        cfg = AutoscaleConfig(
            enabled=True,
            min_workers=0,
            max_workers=8,
            idle_timeout_seconds=60,
        )
        autoscaler = GPUAutoscaler(config=cfg)
        autoscaler._last_activity_time = time.time() - 120
        event = autoscaler.evaluate(current_tasks=0, queue_depth=0)
        assert event.direction == ScalingDirection.DOWN
        assert event.target_workers == 0

    def test_no_scale_down_before_timeout(self) -> None:
        cfg = AutoscaleConfig(
            enabled=True,
            min_workers=0,
            max_workers=8,
            idle_timeout_seconds=999999,
        )
        autoscaler = GPUAutoscaler(config=cfg)
        autoscaler.record_activity()
        event = autoscaler.evaluate(current_tasks=0, queue_depth=0)
        assert event.direction == ScalingDirection.NONE


class TestApply:
    """Test apply method."""

    def test_apply_updates_worker_count(self) -> None:
        cfg = AutoscaleConfig(enabled=True, max_workers=8, idle_timeout_seconds=60)
        autoscaler = GPUAutoscaler(config=cfg)
        autoscaler._last_activity_time = time.time() - 120
        event = autoscaler.evaluate(current_tasks=0, queue_depth=0)
        assert autoscaler.current_workers == 0
        result = autoscaler.apply(event)
        assert result is True
        assert autoscaler.current_workers == 0

    def test_apply_none_returns_false(self) -> None:
        autoscaler = GPUAutoscaler()
        event = ScalingEvent(
            direction=ScalingDirection.NONE,
            previous_workers=0,
            target_workers=0,
            reason="no change",
        )
        result = autoscaler.apply(event)
        assert result is False

    def test_get_scaling_history(self) -> None:
        cfg = AutoscaleConfig(
            enabled=True, max_workers=8, gpu_increment=0.5, idle_timeout_seconds=60
        )
        autoscaler = GPUAutoscaler(config=cfg)
        autoscaler._last_activity_time = time.time() - 120
        event = autoscaler.evaluate(current_tasks=0, queue_depth=0)
        autoscaler.apply(event)
        history = autoscaler.get_scaling_history()
        assert len(history) == 1
        assert history[0].direction == ScalingDirection.DOWN


class TestAutoscaleConfigValidation:
    """Test AutoscaleConfig field validation."""

    def test_max_workers_minimum(self) -> None:
        with pytest.raises(ValueError, match="max_workers"):
            AutoscaleConfig(max_workers=0)

    def test_timeout_minimum(self) -> None:
        with pytest.raises(ValueError, match="timeout"):
            AutoscaleConfig(scale_up_timeout_seconds=30)

    def test_spot_preference_range(self) -> None:
        with pytest.raises(ValueError, match="spot_preference"):
            AutoscaleConfig(spot_preference=1.5)
        with pytest.raises(ValueError, match="spot_preference"):
            AutoscaleConfig(spot_preference=-0.1)

    def test_gpu_increment_valid_values(self) -> None:
        AutoscaleConfig(gpu_increment=0.5)  # valid
        AutoscaleConfig(gpu_increment=1.0)  # valid
        with pytest.raises(ValueError, match="gpu_increment"):
            AutoscaleConfig(gpu_increment=0.25)
        with pytest.raises(ValueError, match="gpu_increment"):
            AutoscaleConfig(gpu_increment=2.0)
