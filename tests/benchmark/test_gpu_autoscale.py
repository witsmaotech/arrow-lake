"""Benchmark tests for GPUAutoscaler — cold start, idle scale-down, pulse load."""



from __future__ import annotations

import pytest

pytestmark = pytest.mark.benchmark

import time

import pytest

from arrow_lake.config import AutoscaleConfig
from arrow_lake.ray_runtime.autoscaler import GPUAutoscaler, ScalingDirection


class TestColdStart:
    """Scenario: 0 → N GPU workers from cold start."""

    def test_zero_to_two_workers(self) -> None:
        config = AutoscaleConfig(
            enabled=True,
            min_workers=0,
            max_workers=8,
            cooldown_period=0.0,
            idle_timeout_seconds=60,
        )
        scaler = GPUAutoscaler(config)
        assert scaler.current_workers == 0

        event = scaler.evaluate(
            current_tasks=0,
            available_gpus=0,
            queue_depth=10,
        )
        assert event.direction == ScalingDirection.UP
        assert event.target_workers >= 2

        scaler.apply(event)
        assert scaler.current_workers >= 2

    def test_cold_start_respects_max_workers(self) -> None:
        config = AutoscaleConfig(
            enabled=True,
            min_workers=0,
            max_workers=2,
            cooldown_period=0.0,
            idle_timeout_seconds=60,
        )
        scaler = GPUAutoscaler(config)

        event = scaler.evaluate(
            current_tasks=0,
            available_gpus=0,
            queue_depth=100,
        )
        assert event.target_workers <= 2


class TestIdleScaleDown:
    """Scenario: N → 0 workers after idle timeout."""

    def test_scale_down_after_idle(self) -> None:
        config = AutoscaleConfig(
            enabled=True,
            min_workers=0,
            max_workers=8,
            cooldown_period=0.0,
            idle_timeout_seconds=60,
            scale_down_protection=False,
        )
        scaler = GPUAutoscaler(config)

        # Scale up first
        event = scaler.evaluate(queue_depth=5)
        assert event.direction == ScalingDirection.UP
        scaler.apply(event)
        assert scaler.current_workers > 0

        # Simulate idle timeout by rewinding activity clock
        scaler._last_activity_time = time.time() - 120  # past idle_timeout

        event = scaler.evaluate(current_tasks=0, queue_depth=0)
        assert event.direction == ScalingDirection.DOWN
        assert event.target_workers == 0

    def test_scale_down_protection_blocks_with_active_tasks(self) -> None:
        config = AutoscaleConfig(
            enabled=True,
            min_workers=0,
            max_workers=8,
            cooldown_period=0.0,
            idle_timeout_seconds=60,
            scale_down_protection=True,
        )
        scaler = GPUAutoscaler(config)

        # Scale up
        event = scaler.evaluate(queue_depth=5)
        scaler.apply(event)

        # Past idle timeout but tasks still running
        scaler._last_activity_time = time.time() - 120
        event = scaler.evaluate(current_tasks=3, queue_depth=0)
        assert event.direction == ScalingDirection.NONE
        assert "protection" in event.reason


class TestPulseLoad:
    """Scenario: Rapid scale-up / scale-down cycles (stability test)."""

    def test_cooldown_prevents_flapping(self) -> None:
        config = AutoscaleConfig(
            enabled=True,
            min_workers=0,
            max_workers=8,
            cooldown_period=10.0,
            idle_timeout_seconds=60,
        )
        scaler = GPUAutoscaler(config)

        # First scale-up succeeds
        event = scaler.evaluate(queue_depth=5)
        assert event.direction == ScalingDirection.UP
        scaler.apply(event)

        # Immediate second evaluation is blocked by cooldown
        event = scaler.evaluate(queue_depth=0, current_tasks=0)
        assert event.direction == ScalingDirection.NONE
        assert "cooldown" in event.reason

    def test_multiple_scale_cycles(self) -> None:
        config = AutoscaleConfig(
            enabled=True,
            min_workers=0,
            max_workers=8,
            cooldown_period=0.0,
            idle_timeout_seconds=60,
            scale_down_protection=False,
        )
        scaler = GPUAutoscaler(config)

        # Cycle 1: scale up
        event = scaler.evaluate(queue_depth=10)
        scaler.apply(event)
        up1 = scaler.current_workers

        # Cycle 1: scale down
        scaler._last_activity_time = time.time() - 120
        event = scaler.evaluate(current_tasks=0, queue_depth=0)
        scaler.apply(event)
        assert scaler.current_workers == 0

        # Cycle 2: scale up again
        scaler._last_activity_time = time.time()
        event = scaler.evaluate(queue_depth=5)
        scaler.apply(event)
        assert scaler.current_workers > 0

        # History should have 3 events
        history = scaler.get_scaling_history()
        assert len(history) == 3


class TestEventPersistence:
    """Verify scaling events are persisted and retrievable."""

    def test_event_history_grows(self) -> None:
        config = AutoscaleConfig(
            enabled=True,
            cooldown_period=0.0,
            idle_timeout_seconds=60,
            scale_down_protection=False,
        )
        scaler = GPUAutoscaler(config)

        for _ in range(3):
            event = scaler.evaluate(queue_depth=5)
            if event.direction != ScalingDirection.NONE:
                scaler.apply(event)
                break

        assert len(scaler.get_scaling_history()) == 1

    def test_event_json_serialization(self) -> None:
        config = AutoscaleConfig(enabled=True, cooldown_period=0.0)
        scaler = GPUAutoscaler(config)
        event = scaler.evaluate(queue_depth=10)
        scaler.apply(event)

        history = scaler.get_scaling_history()
        json_str = history[0].to_json()
        assert '"direction"' in json_str
        assert '"target_workers"' in json_str
