"""Tests for Story 6.4 — Error Classification."""

from __future__ import annotations

import pytest
from arrow_lake.exceptions import (
    EmbeddingError,
    ErrorCode,
    HttpError,
    RayRuntimeError,
    StorageError,
    ValidationError,
    WorkflowError,
)
from arrow_lake.workflow.error_handler import (
    ClassifiedError,
    ErrorCategory,
    catch_handler,
    classify_error,
)


class TestClassifyTransientErrors:
    """Test classification of TRANSIENT errors."""

    def test_http_timeout(self) -> None:
        exc = HttpError(error_code=ErrorCode.HTTP_TIMEOUT, message="timeout")
        result = classify_error(exc)
        assert result.category == ErrorCategory.TRANSIENT
        assert result.should_retry is True

    def test_http_rate_limited(self) -> None:
        exc = HttpError(error_code=ErrorCode.HTTP_RATE_LIMITED, message="rate limited")
        result = classify_error(exc)
        assert result.category == ErrorCategory.TRANSIENT
        assert result.should_retry is True

    def test_ray_actor_dead(self) -> None:
        exc = RayRuntimeError(error_code=ErrorCode.RAY_RUNTIME_ACTOR_DEAD, message="dead")
        result = classify_error(exc)
        assert result.category == ErrorCategory.TRANSIENT
        assert result.should_retry is True

    def test_ray_worker_preempted(self) -> None:
        exc = RayRuntimeError(
            error_code=ErrorCode.RAY_RUNTIME_WORKER_PREEMPTED, message="preempted"
        )
        result = classify_error(exc)
        assert result.category == ErrorCategory.TRANSIENT
        assert result.should_retry is True
        assert result.retry_max_attempts == 5

    def test_dead_letter_write_failed(self) -> None:
        from arrow_lake.exceptions import QualityError

        exc = QualityError(error_code=ErrorCode.QUALITY_DEAD_LETTER_WRITE_FAILED, message="fail")
        result = classify_error(exc)
        assert result.category == ErrorCategory.TRANSIENT


class TestClassifyResourceErrors:
    """Test classification of RESOURCE errors."""

    def test_ray_placement_failed(self) -> None:
        exc = RayRuntimeError(
            error_code=ErrorCode.RAY_RUNTIME_PLACEMENT_FAILED, message="placement"
        )
        result = classify_error(exc)
        assert result.category == ErrorCategory.RESOURCE
        assert result.should_retry is True
        assert result.retry_max_attempts == 5

    def test_ray_object_store_full(self) -> None:
        exc = RayRuntimeError(error_code=ErrorCode.RAY_RUNTIME_OBJECT_STORE_FULL, message="full")
        result = classify_error(exc)
        assert result.category == ErrorCategory.RESOURCE

    def test_storage_connection_failed(self) -> None:
        exc = StorageError(error_code=ErrorCode.STORAGE_CONNECTION_FAILED, message="conn")
        result = classify_error(exc)
        assert result.category == ErrorCategory.RESOURCE


class TestClassifyValidationErrors:
    """Test classification of VALIDATION errors."""

    def test_invalid_config(self) -> None:
        exc = ValidationError(error_code=ErrorCode.VALIDATION_INVALID_CONFIG, message="bad config")
        result = classify_error(exc)
        assert result.category == ErrorCategory.VALIDATION
        assert result.should_retry is False

    def test_missing_field(self) -> None:
        exc = ValidationError(error_code=ErrorCode.VALIDATION_MISSING_FIELD, message="missing")
        result = classify_error(exc)
        assert result.category == ErrorCategory.VALIDATION

    def test_schema_unknown_column(self) -> None:
        from arrow_lake.exceptions import QualityError

        exc = QualityError(error_code=ErrorCode.QUALITY_SCHEMA_UNKNOWN_COLUMN, message="unknown")
        result = classify_error(exc)
        assert result.category == ErrorCategory.VALIDATION

    def test_schema_type_mismatch(self) -> None:
        from arrow_lake.exceptions import QualityError

        exc = QualityError(error_code=ErrorCode.QUALITY_SCHEMA_TYPE_MISMATCH, message="mismatch")
        result = classify_error(exc)
        assert result.category == ErrorCategory.VALIDATION

    def test_vector_dimension_mismatch(self) -> None:
        from arrow_lake.exceptions import QueryError

        exc = QueryError(error_code=ErrorCode.VECTOR_DIMENSION_MISMATCH, message="dim")
        result = classify_error(exc)
        assert result.category == ErrorCategory.VALIDATION


class TestClassifyFatalErrors:
    """Test classification of FATAL errors."""

    def test_storage_write_failed(self) -> None:
        exc = StorageError(error_code=ErrorCode.STORAGE_WRITE_FAILED, message="write fail")
        result = classify_error(exc)
        assert result.category == ErrorCategory.FATAL
        assert result.should_retry is False

    def test_embedding_model_error(self) -> None:
        exc = EmbeddingError(error_code=ErrorCode.EMBEDDING_MODEL_ERROR, message="model fail")
        result = classify_error(exc)
        assert result.category == ErrorCategory.FATAL

    def test_workflow_rollback_failed(self) -> None:
        exc = WorkflowError(error_code=ErrorCode.WORKFLOW_STATE_ROLLBACK_FAILED, message="rollback")
        result = classify_error(exc)
        assert result.category == ErrorCategory.FATAL


class TestClassifyNonArrowLakeErrors:
    """Test classification of non-ArrowLakeError exceptions."""

    def test_connection_error(self) -> None:
        result = classify_error(ConnectionError("network"))
        assert result.category == ErrorCategory.TRANSIENT
        assert result.should_retry is True

    def test_timeout_error(self) -> None:
        result = classify_error(TimeoutError("timed out"))
        assert result.category == ErrorCategory.TRANSIENT

    def test_os_error(self) -> None:
        result = classify_error(OSError("io error"))
        assert result.category == ErrorCategory.TRANSIENT

    def test_value_error(self) -> None:
        result = classify_error(ValueError("bad value"))
        assert result.category == ErrorCategory.VALIDATION
        assert result.should_retry is False

    def test_key_error(self) -> None:
        result = classify_error(KeyError("missing key"))
        assert result.category == ErrorCategory.VALIDATION

    def test_unknown_error_as_fatal(self) -> None:
        result = classify_error(RuntimeError("unknown"))
        assert result.category == ErrorCategory.FATAL


class TestClassifiedError:
    """Test ClassifiedError frozen dataclass."""

    def test_frozen(self) -> None:
        exc = OSError("test")
        result = ClassifiedError(
            category=ErrorCategory.TRANSIENT,
            exception=exc,
            should_retry=True,
            retry_max_attempts=3,
            reason="test reason",
        )
        with pytest.raises(AttributeError):
            result.category = ErrorCategory.FATAL

    def test_should_retry_field(self) -> None:
        assert (
            ClassifiedError(
                category=ErrorCategory.TRANSIENT,
                exception=OSError(""),
                should_retry=True,
            ).should_retry
            is True
        )

        assert (
            ClassifiedError(
                category=ErrorCategory.FATAL,
                exception=RuntimeError(""),
                should_retry=False,
            ).should_retry
            is False
        )


class TestCatchHandler:
    """Test catch_handler logging."""

    def test_catch_handler_logs(self) -> None:
        exc = ConnectionError("network down")
        catch_handler(exc)  # Should not raise
