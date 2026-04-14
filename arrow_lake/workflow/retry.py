"""Transient retry with exponential backoff (Story 6.3).

Provides two retry mechanisms:
1. ``build_metaflow_retry`` — Metaflow ``@retry`` StepDecorator for step-level retries.
2. ``retry_with_backoff`` — tenacity decorator for intra-step retries (e.g. Ray actors).
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import Any

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


class RetryCategory(StrEnum):
    """Categories of retryable errors."""

    TRANSIENT = "transient"
    RESOURCE = "resource"
    SPOT_PREEMPTION = "spot"


def build_metaflow_retry(
    times: int = 3,
    minutes_between_retries: int = 2,
) -> Any:
    """Build a Metaflow ``@retry`` StepDecorator with configured settings.

    Wraps ``metaflow.plugins.retry_decorator.RetryDecorator`` which
    follows the StepDecorator protocol (name + defaults class attributes).

    Args:
        times: Number of retries.
        minutes_between_retries: Minutes between retries.

    Returns:
        Decorator function for use on ``@step`` methods.
    """
    from metaflow.plugins.retry_decorator import RetryDecorator

    class ConfiguredRetry(RetryDecorator):
        name = "retry"
        defaults = {  # noqa: RUF012 — follows Metaflow StepDecorator protocol
            "times": str(times),
            "minutes_between_retries": str(minutes_between_retries),
        }

    def _decorator(func: Callable[..., Any]) -> Any:
        return ConfiguredRetry()(func)

    return _decorator


def retry_with_backoff(
    max_attempts: int = 3,
    min_backoff: float = 1.0,
    max_backoff: float = 60.0,
    retryable_exceptions: tuple[type[Exception], ...] = (OSError, ConnectionError),
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Create a tenacity retry decorator with exponential backoff.

    Follows the existing pattern from ``embed/encoder.py`` and
    ``ingest/connectors_http.py``.

    Args:
        max_attempts: Maximum number of attempts (including initial).
        min_backoff: Minimum backoff in seconds.
        max_backoff: Maximum backoff in seconds.
        retryable_exceptions: Exception types to retry on.

    Returns:
        Tenacity retry decorator.
    """
    return retry(
        retry=retry_if_exception_type(retryable_exceptions),
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=1, min=min_backoff, max=max_backoff),
        reraise=True,
    )
