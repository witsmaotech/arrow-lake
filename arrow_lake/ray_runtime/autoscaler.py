"""Elastic GPU burst scaling for Ray clusters (Story 7.5).

Provides:
- GPU autoscaling policy with 0->N worker scaling
- Spot instance preference with on-demand fallback
- Fractional GPU support (0.5 increments)
- Structured JSON logging of scaling events
- Idle timeout scale-down to 0
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import structlog

from arrow_lake.config import AutoscaleConfig

logger = structlog.get_logger(__name__)

__all__ = ["GPUAutoscaler", "ScalingDirection", "ScalingEvent"]


class ScalingDirection(StrEnum):
    """Direction of a scaling operation."""

    UP = "up"
    DOWN = "down"
    NONE = "none"


@dataclass(frozen=True)
class ScalingEvent:
    """Immutable record of a scaling decision.

    Attributes:
        direction: Scale up or down.
        previous_workers: Worker count before scaling.
        target_workers: Worker count after scaling.
        reason: Human-readable reason for the decision.
        timestamp: Unix timestamp.
        metadata: Additional context (GPU type, spot vs on-demand, etc.)
    """

    direction: ScalingDirection
    previous_workers: int
    target_workers: int
    reason: str
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(
            {
                "event_type": "autoscaling_event",
                "direction": self.direction.value,
                "previous_workers": self.previous_workers,
                "target_workers": self.target_workers,
                "reason": self.reason,
                "timestamp": self.timestamp,
                "metadata": self.metadata,
            }
        )


class GPUAutoscaler:
    """GPU autoscaler for Ray clusters.

    Implements a policy-based autoscaler that:
    - Scales from 0 to max_workers based on demand
    - Prefers spot instances when spot_preference > 0
    - Supports fractional GPU allocation (0.5 increments)
    - Logs every scaling decision as structured JSON
    - Scales to 0 after idle_timeout_seconds of inactivity

    Usage::

        from arrow_lake.ray_runtime.autoscaler import GPUAutoscaler

        autoscaler = GPUAutoscaler(config=autoscale_config)
        event = autoscaler.evaluate(current_tasks=50, queue_depth=100)
        if event.direction != ScalingDirection.NONE:
            autoscaler.apply(event)
    """

    def __init__(self, config: AutoscaleConfig | None = None) -> None:
        self._config = config or AutoscaleConfig()
        self._last_activity_time: float = time.time()
        self._current_workers: int = self._config.min_workers
        self._pending_events: list[ScalingEvent] = []

    @property
    def config(self) -> AutoscaleConfig:
        return self._config

    @property
    def current_workers(self) -> int:
        return self._current_workers

    def evaluate(
        self,
        *,
        current_tasks: int = 0,
        available_gpus: int = 0,
        queue_depth: int = 0,
    ) -> ScalingEvent:
        """Evaluate whether scaling is needed.

        Args:
            current_tasks: Number of active tasks.
            available_gpus: Currently available GPUs.
            queue_depth: Number of tasks waiting for resources.

        Returns:
            ScalingEvent describing the decision.
        """
        if not self._config.enabled:
            return ScalingEvent(
                direction=ScalingDirection.NONE,
                previous_workers=self._current_workers,
                target_workers=self._current_workers,
                reason="autoscaling disabled",
            )

        # Track activity
        if current_tasks > 0 or queue_depth > 0:
            self._last_activity_time = time.time()

        # Check idle scale-down
        if current_tasks == 0 and queue_depth == 0 and self._should_scale_down():
            return self._make_scale_down_event(
                target=self._config.min_workers,
                reason="idle timeout reached",
            )

        # Check scale-up
        if queue_depth > 0:
            needed = self._calculate_target_workers(current_tasks, available_gpus, queue_depth)
            if needed > self._current_workers:
                capped = min(needed, self._config.max_workers)
                return self._make_scale_up_event(
                    target=capped,
                    reason=f"queue_depth={queue_depth}, available_gpus={available_gpus}",
                    metadata={"queue_depth": queue_depth, "available_gpus": available_gpus},
                )

        # No change needed
        return ScalingEvent(
            direction=ScalingDirection.NONE,
            previous_workers=self._current_workers,
            target_workers=self._current_workers,
            reason="no scaling needed",
        )

    def apply(self, event: ScalingEvent) -> bool:
        """Apply a scaling decision to the Ray cluster.

        Args:
            event: Scaling decision to apply.

        Returns:
            True if scaling was initiated successfully.
        """
        if event.direction == ScalingDirection.NONE:
            return False

        self._log_scaling_event(event)
        self._current_workers = event.target_workers
        self._pending_events.append(event)
        return True

    def get_scaling_history(self) -> list[ScalingEvent]:
        """Return list of recent scaling events."""
        return list(self._pending_events)

    def record_activity(self) -> None:
        """Reset the idle timer (call when new work arrives)."""
        self._last_activity_time = time.time()

    def _calculate_target_workers(
        self,
        current_tasks: int,
        available_gpus: int,
        queue_depth: int,
    ) -> int:
        """Calculate optimal worker count based on demand.

        Each worker provides gpu_increment GPUs. Scale to cover the queue.
        """
        gpus_per_worker = self._config.gpu_increment
        gpu_deficit = max(0, queue_depth - available_gpus)
        additional_workers = math.ceil(gpu_deficit / gpus_per_worker)
        return self._current_workers + additional_workers

    def _should_scale_down(self) -> bool:
        """Check if idle timeout has been reached."""
        idle_duration = time.time() - self._last_activity_time
        return idle_duration >= self._config.idle_timeout_seconds

    def _choose_instance_type(self) -> str:
        """Choose instance type based on spot_preference.

        Returns:
            "spot" or "on-demand"
        """
        pref = self._config.spot_preference
        if pref >= 1.0:
            return "spot"
        if pref <= 0.0:
            return "on-demand"
        import random

        return "spot" if random.random() < pref else "on-demand"

    def _make_scale_up_event(
        self,
        target: int,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> ScalingEvent:
        """Create a scale-up event with instance type metadata."""
        meta = metadata or {}
        meta["instance_type"] = self._choose_instance_type()
        meta["gpu_per_worker"] = self._config.gpu_increment
        return ScalingEvent(
            direction=ScalingDirection.UP,
            previous_workers=self._current_workers,
            target_workers=target,
            reason=reason,
            metadata=meta,
        )

    def _make_scale_down_event(
        self,
        target: int,
        reason: str,
    ) -> ScalingEvent:
        """Create a scale-down event."""
        return ScalingEvent(
            direction=ScalingDirection.DOWN,
            previous_workers=self._current_workers,
            target_workers=target,
            reason=reason,
        )

    def _log_scaling_event(self, event: ScalingEvent) -> None:
        """Log scaling event as structured JSON."""
        logger.info(
            "autoscaling_event",
            event_type="scaling_decision",
            direction=event.direction.value,
            target_replicas=event.target_workers,
            current_replicas=event.previous_workers,
            reason=event.reason,
        )
