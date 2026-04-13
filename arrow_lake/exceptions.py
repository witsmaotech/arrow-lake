"""Arrow Lake custom exception hierarchy.

All exceptions inherit from ArrowLakeError. Each includes:
- error_code: ErrorCode enum for programmatic handling
- message: Human-readable description
- context: Optional dict with debugging context
"""

from enum import StrEnum


class ErrorCode(StrEnum):
    """Machine-readable error codes for all Arrow Lake exceptions.

    Naming: {CATEGORY}_{SPECIFIC_ERROR}
    """

    # Storage errors
    STORAGE_CONNECTION_FAILED = "STORAGE_CONNECTION_FAILED"
    STORAGE_WRITE_FAILED = "STORAGE_WRITE_FAILED"
    STORAGE_READ_FAILED = "STORAGE_READ_FAILED"
    STORAGE_PATH_NOT_FOUND = "STORAGE_PATH_NOT_FOUND"

    # Query errors
    QUERY_TIMEOUT = "QUERY_TIMEOUT"
    QUERY_SYNTAX_ERROR = "QUERY_SYNTAX_ERROR"
    QUERY_NO_RESULTS = "QUERY_NO_RESULTS"
    QUERY_INDEX_NOT_FOUND = "QUERY_INDEX_NOT_FOUND"

    # Ingestion errors
    INGEST_SCHEMA_MISMATCH = "INGEST_SCHEMA_MISMATCH"
    INGEST_UNSUPPORTED_FORMAT = "INGEST_UNSUPPORTED_FORMAT"
    INGEST_FILE_NOT_FOUND = "INGEST_FILE_NOT_FOUND"
    INGEST_DUPLICATE_KEY = "INGEST_DUPLICATE_KEY"

    # Catalog errors
    CATALOG_DATASET_NOT_FOUND = "CATALOG_DATASET_NOT_FOUND"
    CATALOG_DATASET_ALREADY_EXISTS = "CATALOG_DATASET_ALREADY_EXISTS"
    CATALOG_RATE_LIMITED = "CATALOG_RATE_LIMITED"
    CATALOG_CONNECTION_FAILED = "CATALOG_CONNECTION_FAILED"

    # Ray runtime errors
    RAY_RUNTIME_ACTOR_DEAD = "RAY_RUNTIME_ACTOR_DEAD"
    RAY_RUNTIME_PLACEMENT_FAILED = "RAY_RUNTIME_PLACEMENT_FAILED"
    RAY_RUNTIME_OBJECT_STORE_FULL = "RAY_RUNTIME_OBJECT_STORE_FULL"
    RAY_RUNTIME_WORKER_PREEMPTED = "RAY_RUNTIME_WORKER_PREEMPTED"

    # Validation errors
    VALIDATION_INVALID_CONFIG = "VALIDATION_INVALID_CONFIG"
    VALIDATION_MISSING_FIELD = "VALIDATION_MISSING_FIELD"
    VALIDATION_TYPE_ERROR = "VALIDATION_TYPE_ERROR"
    VALIDATION_SCHEMA_EVOLUTION = "VALIDATION_SCHEMA_EVOLUTION"


class ArrowLakeError(Exception):
    """Base exception for all Arrow Lake errors.

    Attributes:
        error_code: Machine-readable error classification.
        message: Human-readable error description.
        context: Optional dict with additional debugging context.
    """

    def __init__(
        self,
        error_code: ErrorCode,
        message: str,
        context: dict[str, object] | None = None,
    ) -> None:
        self.error_code = error_code
        self.message = message
        self.context = context if context is not None else {}
        super().__init__(f"[{error_code.value}] {message}")

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"error_code={self.error_code.value!r}, "
            f"message={self.message!r}, "
            f"context={self.context!r})"
        )


class StorageError(ArrowLakeError):
    """Errors related to storage operations (Lance, S3, MinIO)."""


class QueryError(ArrowLakeError):
    """Errors related to search and OLAP queries."""


class IngestError(ArrowLakeError):
    """Errors related to data ingestion pipelines."""


class CatalogError(ArrowLakeError):
    """Errors related to the Catalog Actor and metadata operations."""


class RayRuntimeError(ArrowLakeError):
    """Errors related to Ray cluster and actor lifecycle."""


class ValidationError(ArrowLakeError):
    """Errors related to configuration, schema, and data validation."""
