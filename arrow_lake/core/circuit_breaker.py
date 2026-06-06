"""Lightweight circuit breaker — no external dependencies.

States: CLOSED (normal) → OPEN (failing) → HALF_OPEN (probing recovery).
"""

from __future__ import annotations

import logging
import threading
import time
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Thread-safe circuit breaker.

    Args:
        failure_threshold: Consecutive failures before opening.
        recovery_timeout: Seconds to wait before half-open probe.
        half_open_max_calls: Max calls allowed in half-open state.
    """

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_max_calls: int = 1,
        name: str = "",
    ) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._half_open_max_calls = half_open_max_calls
        self._name = name or id(self)

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: float = 0.0
        self._half_open_calls = 0
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            if (
                self._state == CircuitState.OPEN
                and time.monotonic() - self._last_failure_time >= self._recovery_timeout
            ):
                self._state = CircuitState.HALF_OPEN
                self._half_open_calls = 0
                logger.info("CircuitBreaker(%s): OPEN → HALF_OPEN", self._name)
            return self._state

    def record_success(self) -> None:
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.CLOSED
                logger.info("CircuitBreaker(%s): HALF_OPEN → CLOSED", self._name)
            self._failure_count = 0
            self._half_open_calls = 0
            self._emit_metrics()

    def record_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            if self._state == CircuitState.HALF_OPEN:
                # Recover the half-open call slot so another probe can be attempted
                self._half_open_calls = max(0, self._half_open_calls - 1)
                self._state = CircuitState.OPEN
                logger.warning("CircuitBreaker(%s): HALF_OPEN → OPEN", self._name)
            elif self._failure_count >= self._failure_threshold:
                self._state = CircuitState.OPEN
                logger.warning(
                    "CircuitBreaker(%s): CLOSED → OPEN after %d failures",
                    self._name, self._failure_count,
                )
            self._emit_metrics()

    def allow_request(self) -> bool:
        with self._lock:
            if self._state == CircuitState.CLOSED:
                return True
            if self._state == CircuitState.OPEN:
                if time.monotonic() - self._last_failure_time >= self._recovery_timeout:
                    self._state = CircuitState.HALF_OPEN
                    self._half_open_calls = 0
                    # Fall through to HALF_OPEN check below
                else:
                    return False
            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_calls < self._half_open_max_calls:
                    self._half_open_calls += 1
                    return True
                return False
            return False

    def _emit_metrics(self) -> None:
        """Emit Prometheus metrics for circuit breaker state and failures."""
        try:
            from arrow_lake.core.metrics import get_metrics_enabled
            if not get_metrics_enabled():
                return
            from arrow_lake.core.metrics import (
                circuit_breaker_state,
                circuit_breaker_failures,
            )
            state_val = {
                CircuitState.CLOSED: 0,
                CircuitState.HALF_OPEN: 1,
                CircuitState.OPEN: 2,
            }.get(self._state, 0)
            circuit_breaker_state.labels(name=str(self._name)).set(state_val)
            if self._failure_count > 0:
                circuit_breaker_failures.labels(name=str(self._name)).inc()
        except Exception:
            pass  # Metrics must never break the circuit breaker

    def __call__(self, fn: Any) -> Any:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not self.allow_request():
                raise RuntimeError(
                    f"CircuitBreaker({self._name}) is OPEN — requests blocked"
                )
            try:
                result = fn(*args, **kwargs)
                self.record_success()
                return result
            except Exception:
                self.record_failure()
                raise

        return wrapper
