"""Unit tests for CircuitBreaker."""

from __future__ import annotations

import time

from arrow_lake.core.circuit_breaker import CircuitBreaker, CircuitState


def test_starts_closed():
    cb = CircuitBreaker(name="test")
    assert cb.state == CircuitState.CLOSED


def test_allow_request_when_closed():
    cb = CircuitBreaker(name="test")
    assert cb.allow_request() is True


def test_opens_after_threshold():
    cb = CircuitBreaker(failure_threshold=3, name="test")
    for _ in range(3):
        cb.record_failure()
    assert cb.state == CircuitState.OPEN


def test_blocks_request_when_open():
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=60.0, name="test")
    cb.record_failure()
    assert cb.allow_request() is False


def test_resets_on_success():
    cb = CircuitBreaker(failure_threshold=3, name="test")
    for _ in range(2):
        cb.record_failure()
    assert cb.state == CircuitState.CLOSED
    cb.record_success()
    assert cb._failure_count == 0


def test_transitions_to_half_open_after_timeout():
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.05, name="test")
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    time.sleep(0.06)
    assert cb.state == CircuitState.HALF_OPEN


def test_half_open_allows_limited_calls():
    cb = CircuitBreaker(
        failure_threshold=1,
        recovery_timeout=0.05,
        half_open_max_calls=2,
        name="test",
    )
    cb.record_failure()
    time.sleep(0.06)
    assert cb.state == CircuitState.HALF_OPEN
    assert cb.allow_request() is True
    assert cb.allow_request() is True
    assert cb.allow_request() is False


def test_half_open_closes_on_success():
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.05, name="test")
    cb.record_failure()
    time.sleep(0.06)
    assert cb.state == CircuitState.HALF_OPEN
    cb.allow_request()
    cb.record_success()
    assert cb.state == CircuitState.CLOSED


def test_half_open_reopens_on_failure():
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.05, name="test")
    cb.record_failure()
    time.sleep(0.06)
    assert cb.state == CircuitState.HALF_OPEN
    cb.allow_request()
    cb.record_failure()
    assert cb.state == CircuitState.OPEN


def test_decorator_success():
    cb = CircuitBreaker(name="test")

    @cb
    def add(a, b):
        return a + b

    assert add(2, 3) == 5
    assert cb.state == CircuitState.CLOSED


def test_decorator_failure_opens():
    cb = CircuitBreaker(failure_threshold=2, name="test")

    @cb
    def fail():
        raise ValueError("boom")

    for _ in range(2):
        with pytest.raises(ValueError):
            fail()
    assert cb.state == CircuitState.OPEN


def test_decorator_blocks_when_open():
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=60.0, name="test")

    @cb
    def fail():
        raise ValueError("boom")

    with pytest.raises(ValueError):
        fail()

    with pytest.raises(RuntimeError, match="OPEN"):
        fail()


def test_decorator_recovers_after_timeout():
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.05, name="test")

    @cb
    def fn(ok=False):
        if not ok:
            raise ValueError("boom")
        return "ok"

    with pytest.raises(ValueError):
        fn()
    time.sleep(0.06)
    assert fn(ok=True) == "ok"
    assert cb.state == CircuitState.CLOSED


import pytest
