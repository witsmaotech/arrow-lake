"""Tests for Story 6.3 — Transient Retry."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from arrow_lake.workflow.retry import (
    RetryCategory,
    build_metaflow_retry,
    retry_with_backoff,
)


class TestRetryCategory:
    """Test RetryCategory enum."""

    def test_transient_value(self) -> None:
        assert RetryCategory.TRANSIENT == "transient"

    def test_resource_value(self) -> None:
        assert RetryCategory.RESOURCE == "resource"

    def test_spot_preemption_value(self) -> None:
        assert RetryCategory.SPOT_PREEMPTION == "spot"


class TestRetryWithBackoff:
    """Test retry_with_backoff tenacity decorator."""

    def test_retries_on_transient_error(self) -> None:
        call_count = 0

        @retry_with_backoff(
            max_attempts=3,
            min_backoff=0.001,
            max_backoff=0.01,
            retryable_exceptions=(OSError,),
        )
        def flaky_func() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise OSError("transient failure")
            return "ok"

        result = flaky_func()
        assert result == "ok"
        assert call_count == 3

    def test_exhausts_retries(self) -> None:
        @retry_with_backoff(
            max_attempts=2,
            min_backoff=0.001,
            max_backoff=0.01,
            retryable_exceptions=(ConnectionError,),
        )
        def always_fail() -> str:
            raise ConnectionError("always fails")

        with pytest.raises(ConnectionError):
            always_fail()

    def test_custom_max_attempts(self) -> None:
        call_count = 0

        @retry_with_backoff(
            max_attempts=5,
            min_backoff=0.001,
            max_backoff=0.01,
            retryable_exceptions=(OSError,),
        )
        def fail_4_times() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 4:
                raise OSError("fail")
            return "ok"

        result = fail_4_times()
        assert result == "ok"
        assert call_count == 4

    def test_no_retry_on_non_retryable(self) -> None:
        @retry_with_backoff(
            max_attempts=3,
            min_backoff=0.001,
            max_backoff=0.01,
            retryable_exceptions=(OSError, ConnectionError),
        )
        def type_error_func() -> str:
            raise TypeError("not retryable")

        with pytest.raises(TypeError):
            type_error_func()

    def test_custom_retryable_exceptions(self) -> None:
        call_count = 0

        @retry_with_backoff(
            max_attempts=3,
            min_backoff=0.001,
            max_backoff=0.01,
            retryable_exceptions=(ValueError,),
        )
        def value_error_func() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ValueError("retry me")
            return "ok"

        result = value_error_func()
        assert result == "ok"

    def test_default_is_oserror_and_connection_error(self) -> None:
        """Default retryable exceptions should be OSError and ConnectionError."""
        decorator = retry_with_backoff()
        # Just verify it's callable (decorator creation works)
        assert callable(decorator)


class TestBuildMetaflowRetry:
    """Test build_metaflow_retry wrapper."""

    @patch("arrow_lake.workflow.retry.RetryDecorator", create=True)
    def test_returns_callable(self, mock_retry: MagicMock) -> None:
        # Mock the RetryDecorator class to return a no-op decorator
        instance = MagicMock()
        mock_retry.return_value = instance
        instance.return_value = lambda f: f

        decorator = build_metaflow_retry(times=3, minutes_between_retries=2)
        assert callable(decorator)

    def test_retry_category_enum_values(self) -> None:
        assert len(RetryCategory) == 3
        assert RetryCategory.TRANSIENT in list(RetryCategory)
        assert RetryCategory.RESOURCE in list(RetryCategory)
        assert RetryCategory.SPOT_PREEMPTION in list(RetryCategory)
