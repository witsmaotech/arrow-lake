"""Error classification for workflow steps (Story 6.4).

Classifies exceptions into TRANSIENT, RESOURCE, VALIDATION, and FATAL
categories to enable appropriate handling strategies:
- TRANSIENT: auto-retry
- RESOURCE: retry with longer backoff
- VALIDATION: no retry, log and continue
- FATAL: no retry, fail the flow
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import structlog

from arrow_lake.exceptions import ArrowLakeError, ErrorCode

logger = structlog.get_logger(__name__)


class ErrorCategory(StrEnum):
    """Error classification categories."""

    TRANSIENT = "TRANSIENT"
    RESOURCE = "RESOURCE"
    VALIDATION = "VALIDATION"
    FATAL = "FATAL"


@dataclass(frozen=True)
class ClassifiedError:
    """Result of error classification."""

    category: ErrorCategory
    exception: Exception
    should_retry: bool
    retry_max_attempts: int = 3
    reason: str = ""


_ERROR_CLASSIFICATION: dict[ErrorCode, tuple[ErrorCategory, bool, int]] = {
    # Transient errors — auto-retry
    ErrorCode.HTTP_TIMEOUT: (ErrorCategory.TRANSIENT, True, 3),
    ErrorCode.HTTP_RATE_LIMITED: (ErrorCategory.TRANSIENT, True, 5),
    ErrorCode.RAY_RUNTIME_ACTOR_DEAD: (ErrorCategory.TRANSIENT, True, 3),
    ErrorCode.RAY_RUNTIME_WORKER_PREEMPTED: (ErrorCategory.TRANSIENT, True, 5),
    ErrorCode.QUALITY_DEAD_LETTER_WRITE_FAILED: (ErrorCategory.TRANSIENT, True, 3),
    # Resource errors — retry with longer backoff
    ErrorCode.RAY_RUNTIME_PLACEMENT_FAILED: (ErrorCategory.RESOURCE, True, 5),
    ErrorCode.RAY_RUNTIME_OBJECT_STORE_FULL: (ErrorCategory.RESOURCE, True, 3),
    ErrorCode.STORAGE_CONNECTION_FAILED: (ErrorCategory.RESOURCE, True, 3),
    # Validation errors — no retry
    ErrorCode.VALIDATION_INVALID_CONFIG: (ErrorCategory.VALIDATION, False, 0),
    ErrorCode.VALIDATION_MISSING_FIELD: (ErrorCategory.VALIDATION, False, 0),
    ErrorCode.VALIDATION_TYPE_ERROR: (ErrorCategory.VALIDATION, False, 0),
    ErrorCode.QUALITY_SCHEMA_UNKNOWN_COLUMN: (ErrorCategory.VALIDATION, False, 0),
    ErrorCode.QUALITY_SCHEMA_TYPE_MISMATCH: (ErrorCategory.VALIDATION, False, 0),
    ErrorCode.INGEST_SCHEMA_MISMATCH: (ErrorCategory.VALIDATION, False, 0),
    ErrorCode.VECTOR_DIMENSION_MISMATCH: (ErrorCategory.VALIDATION, False, 0),
    # Fatal errors — no retry, fail flow
    ErrorCode.STORAGE_WRITE_FAILED: (ErrorCategory.FATAL, False, 0),
    ErrorCode.EMBEDDING_MODEL_ERROR: (ErrorCategory.FATAL, False, 0),
    ErrorCode.VECTOR_INVALID_QUERY: (ErrorCategory.FATAL, False, 0),
    ErrorCode.WORKFLOW_STATE_ROLLBACK_FAILED: (ErrorCategory.FATAL, False, 0),
}


def classify_error(exc: Exception) -> ClassifiedError:
    """Classify an exception into a workflow error category.

    Args:
        exc: Exception to classify.

    Returns:
        ClassifiedError with category, retry decision, and reason.
    """
    if isinstance(exc, ArrowLakeError) and exc.error_code in _ERROR_CLASSIFICATION:
        category, should_retry, retry_max = _ERROR_CLASSIFICATION[exc.error_code]
        return ClassifiedError(
            category=category,
            exception=exc,
            should_retry=should_retry,
            retry_max_attempts=retry_max,
            reason=f"ErrorCode {exc.error_code.value} classified as {category.value}",
        )

    # Non-ArrowLake exceptions: classify by type
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return ClassifiedError(
            category=ErrorCategory.TRANSIENT,
            exception=exc,
            should_retry=True,
            retry_max_attempts=3,
            reason="Network/IO error classified as TRANSIENT",
        )

    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return ClassifiedError(
            category=ErrorCategory.VALIDATION,
            exception=exc,
            should_retry=False,
            reason="Data error classified as VALIDATION",
        )

    return ClassifiedError(
        category=ErrorCategory.FATAL,
        exception=exc,
        should_retry=False,
        reason="Unknown error type, defaulting to FATAL",
    )


def catch_handler(exc: Exception) -> None:
    """Classify an error and log with structured context.

    Designed to be called from Metaflow ``@catch`` handlers.

    Args:
        exc: Caught exception.
    """
    classified = classify_error(exc)
    logger.error(
        "workflow_step_error_classified",
        category=classified.category.value,
        should_retry=classified.should_retry,
        reason=classified.reason,
        error=str(exc),
    )
