"""Circuit breaker protection for external services (v1.6.0 Phase 3).

LLM/Embedding already have their own circuit breakers.
This module covers Gravitino, HugeGraph, and Redis."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from arrow_lake.core.circuit_breaker import CircuitBreaker

SERVICE_CIRCUIT_BREAKERS: dict[str, CircuitBreaker] = {
    "gravitino": CircuitBreaker(name="gravitino", failure_threshold=5, recovery_timeout=30),
    "hugegraph": CircuitBreaker(name="hugegraph", failure_threshold=5, recovery_timeout=30),
    "redis": CircuitBreaker(name="redis", failure_threshold=3, recovery_timeout=10),
}


@contextmanager
def circuit_protected(service: str) -> Generator[CircuitBreaker, None, None]:
    """Context manager that records success/failure on the circuit breaker for *service*."""
    cb = SERVICE_CIRCUIT_BREAKERS[service]
    cb.allow_request()
    try:
        yield cb
        cb.record_success()
    except Exception:
        cb.record_failure()
        raise
