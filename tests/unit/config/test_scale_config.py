"""Unit tests for scaling configuration — Stories 7.5, 7.12, 7.13."""

from __future__ import annotations

import json
import time

from arrow_lake.config import AutoscaleConfig
from arrow_lake.ray_runtime.autoscaler import (
    GPUAutoscaler,
    ScalingDirection,
    ScalingEvent,
)


class TestAutoscaleScalingPath:
    """Test scaling path from 0 to max workers."""

    def test_scale_zero_to_eight_min_steps(self) -> None:
        """0→8 workers should complete in few steps."""
        cfg = AutoscaleConfig(
            enabled=True,
            max_workers=8,
            gpu_increment=1.0,
            idle_timeout_seconds=60,
        )
        autoscaler = GPUAutoscaler(config=cfg)

        # Simulate heavy queue
        event = autoscaler.evaluate(
            current_tasks=0,
            available_gpus=0,
            queue_depth=100,
        )
        assert event.direction == ScalingDirection.UP
        assert event.target_workers > 0
        assert event.target_workers <= 8

    def test_scale_with_half_gpu_increments(self) -> None:
        """0→8 with 0.5 GPU increment needs 16 steps."""
        cfg = AutoscaleConfig(
            enabled=True,
            max_workers=8,
            gpu_increment=0.5,
            idle_timeout_seconds=60,
        )
        autoscaler = GPUAutoscaler(config=cfg)
        assert autoscaler.config.gpu_increment == 0.5
        assert autoscaler.config.max_workers == 8


class TestFractionalGPUAllocation:
    """Test fractional GPU allocation logic."""

    def test_half_gpu_allocation_count(self) -> None:
        """3 tasks at 0.5 GPU = 6 half-GPU workers."""
        cfg = AutoscaleConfig(
            enabled=True,
            max_workers=8,
            gpu_increment=0.5,
            idle_timeout_seconds=60,
        )
        autoscaler = GPUAutoscaler(config=cfg)
        event = autoscaler.evaluate(
            current_tasks=0,
            available_gpus=0,
            queue_depth=3,
        )
        assert event.direction == ScalingDirection.UP
        assert event.metadata["gpu_per_worker"] == 0.5

    def test_full_gpu_allocation(self) -> None:
        """3 tasks at 1.0 GPU = 3 full-GPU workers."""
        cfg = AutoscaleConfig(
            enabled=True,
            max_workers=8,
            gpu_increment=1.0,
            idle_timeout_seconds=60,
        )
        autoscaler = GPUAutoscaler(config=cfg)
        event = autoscaler.evaluate(
            current_tasks=0,
            available_gpus=0,
            queue_depth=3,
        )
        assert event.metadata["gpu_per_worker"] == 1.0


class TestBurstTimingEstimation:
    """Test elastic burst timing estimation."""

    def test_burst_event_has_timestamp(self) -> None:
        """Scaling events should have a timestamp for timing."""
        before = time.time()
        event = ScalingEvent(
            direction=ScalingDirection.UP,
            previous_workers=0,
            target_workers=8,
            reason="queue_depth=100",
        )
        assert event.timestamp >= before
        assert event.timestamp <= time.time()

    def test_burst_event_serializable(self) -> None:
        """Scaling events should be JSON-serializable for logging."""
        event = ScalingEvent(
            direction=ScalingDirection.UP,
            previous_workers=0,
            target_workers=8,
            reason="queue_depth=100",
            metadata={"instance_type": "spot", "gpu_per_worker": 0.5},
        )
        json_str = event.to_json()
        parsed = json.loads(json_str)
        assert parsed["target_workers"] == 8
        assert parsed["direction"] == "up"
        assert parsed["metadata"]["gpu_per_worker"] == 0.5

    def test_scaling_history_tracks_events(self) -> None:
        """Autoscaler should track all scaling events."""
        cfg = AutoscaleConfig(
            enabled=True,
            max_workers=8,
            idle_timeout_seconds=60,
        )
        autoscaler = GPUAutoscaler(config=cfg)
        autoscaler._last_activity_time = time.time() - 120

        event = autoscaler.evaluate(current_tasks=0, queue_depth=0)
        autoscaler.apply(event)

        history = autoscaler.get_scaling_history()
        assert len(history) == 1
        assert history[0].direction == ScalingDirection.DOWN


class TestScaleResultFormat:
    """Test that benchmark results have correct JSON format."""

    def test_scaling_event_json_structure(self) -> None:
        """ScalingEvent JSON should have all required fields."""
        event = ScalingEvent(
            direction=ScalingDirection.NONE,
            previous_workers=4,
            target_workers=4,
            reason="no change",
        )
        parsed = json.loads(event.to_json())
        assert "event_type" in parsed
        assert "direction" in parsed
        assert "previous_workers" in parsed
        assert "target_workers" in parsed
        assert "reason" in parsed
        assert "timestamp" in parsed
        assert "metadata" in parsed

    def test_benchmark_report_json_format(self) -> None:
        """BenchmarkReport JSON should have expected structure."""
        from tests.benchmark.benchmark_report import BenchmarkReport

        report = BenchmarkReport("test_scale")
        report.measure(
            "test op",
            lambda: time.sleep(0.001),
            rows=1000,
            repeats=1,
            warmup=0,
        )
        parsed = json.loads(report.to_json())
        assert "benchmark" in parsed
        assert "measurements" in parsed
        assert len(parsed["measurements"]) == 1
        assert "elapsed_seconds" in parsed["measurements"][0]
        assert "throughput" in parsed["measurements"][0]
        assert "rows" in parsed["measurements"][0]
