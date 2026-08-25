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

    # HTTP errors (Story 3.2)
    HTTP_FETCH_FAILED = "HTTP_FETCH_FAILED"
    HTTP_TIMEOUT = "HTTP_TIMEOUT"
    HTTP_RATE_LIMITED = "HTTP_RATE_LIMITED"

    # Media errors (Stories 3.3, 3.4)
    IMAGE_DECODE_FAILED = "IMAGE_DECODE_FAILED"
    VIDEO_DECODE_FAILED = "VIDEO_DECODE_FAILED"
    SCENE_DETECTION_FALLBACK = "SCENE_DETECTION_FALLBACK"

    # Embedding errors (Stories 4.1, 4.3)
    EMBEDDING_MODEL_ERROR = "EMBEDDING_MODEL_ERROR"
    EMBEDDING_TIMEOUT = "EMBEDDING_TIMEOUT"
    EMBEDDING_API_ERROR = "EMBEDDING_API_ERROR"
    EMBEDDING_RAY_SERVE_UNAVAILABLE = "EMBEDDING_RAY_SERVE_UNAVAILABLE"
    EMBEDDING_RAY_SERVE_FALLBACK = "EMBEDDING_RAY_SERVE_FALLBACK"

    # Vector search errors (Story 5.1)
    VECTOR_INDEX_FAILED = "VECTOR_INDEX_FAILED"
    VECTOR_SEARCH_FAILED = "VECTOR_SEARCH_FAILED"
    VECTOR_DIMENSION_MISMATCH = "VECTOR_DIMENSION_MISMATCH"
    VECTOR_INDEX_TOO_FEW_ROWS = "VECTOR_INDEX_TOO_FEW_ROWS"
    VECTOR_INVALID_QUERY = "VECTOR_INVALID_QUERY"

    # Scalar index errors (v1.7.1)
    SCALAR_INDEX_FAILED = "SCALAR_INDEX_FAILED"

    # Full-text search errors (Story 5.2)
    FTS_INDEX_FAILED = "FTS_INDEX_FAILED"
    FTS_SEARCH_FAILED = "FTS_SEARCH_FAILED"

    # Hybrid search errors (Story 5.3)
    HYBRID_SEARCH_FAILED = "HYBRID_SEARCH_FAILED"

    # OLAP analytics errors (Story 5.4)
    OLAP_QUERY_FAILED = "OLAP_QUERY_FAILED"
    OLAP_AMBIGUOUS_DATASET = "OLAP_AMBIGUOUS_DATASET"

    # Quality errors (Epic 4)
    QUALITY_FILTER_EXECUTION_ERROR = "QUALITY_FILTER_EXECUTION_ERROR"
    QUALITY_SCHEMA_UNKNOWN_COLUMN = "QUALITY_SCHEMA_UNKNOWN_COLUMN"
    QUALITY_SCHEMA_TYPE_MISMATCH = "QUALITY_SCHEMA_TYPE_MISMATCH"
    QUALITY_EMBEDDING_IMAGE_FAILED = "QUALITY_EMBEDDING_IMAGE_FAILED"
    QUALITY_DEAD_LETTER_WRITE_FAILED = "QUALITY_DEAD_LETTER_WRITE_FAILED"

    # Workflow errors (Epic 6)
    WORKFLOW_STEP_FAILED = "WORKFLOW_STEP_FAILED"
    WORKFLOW_RETRY_EXHAUSTED = "WORKFLOW_RETRY_EXHAUSTED"
    WORKFLOW_STATE_ROLLBACK_FAILED = "WORKFLOW_STATE_ROLLBACK_FAILED"
    WORKFLOW_SCHEDULING_FAILED = "WORKFLOW_SCHEDULING_FAILED"
    WORKFLOW_TAG_CONFLICT = "WORKFLOW_TAG_CONFLICT"
    WORKFLOW_RESUME_FAILED = "WORKFLOW_RESUME_FAILED"
    WORKFLOW_FLOW_NOT_FOUND = "WORKFLOW_FLOW_NOT_FOUND"

    # Argo errors (Sprint 7)
    ARGO_DEPLOY_FAILED = "ARGO_DEPLOY_FAILED"
    ARGO_VALIDATION_FAILED = "ARGO_VALIDATION_FAILED"
    ARGO_GENERATION_FAILED = "ARGO_GENERATION_FAILED"

    # Autoscale errors (Sprint 7)
    AUTOSCALE_FAILED = "AUTOSCALE_FAILED"
    AUTOSCALE_TIMEOUT = "AUTOSCALE_TIMEOUT"

    # SQL query errors (Sprint 8, Story 7.6)
    QUERY_JOIN_NOT_ALLOWED = "QUERY_JOIN_NOT_ALLOWED"
    QUERY_TABLE_NOT_REGISTERED = "QUERY_TABLE_NOT_REGISTERED"

    # Lifecycle errors (Sprint 8, Story 7.7)
    LIFECYCLE_RULE_APPLY_FAILED = "LIFECYCLE_RULE_APPLY_FAILED"
    LIFECYCLE_RESTORE_FAILED = "LIFECYCLE_RESTORE_FAILED"
    LIFECYCLE_CONFIG_INVALID = "LIFECYCLE_CONFIG_INVALID"

    # Faceted search errors (Sprint 8, Story 8.1)
    FACET_QUERY_FAILED = "FACET_QUERY_FAILED"
    FACET_INVALID_COLUMN = "FACET_INVALID_COLUMN"

    # Ensemble search errors (Sprint 9, Story 8.2)
    ENSEMBLE_NO_COLUMNS = "ENSEMBLE_NO_COLUMNS"
    ENSEMBLE_COLUMN_NOT_FOUND = "ENSEMBLE_COLUMN_NOT_FOUND"

    # Lineage errors (Sprint 9, Story 8.3)
    LINEAGE_QUERY_FAILED = "LINEAGE_QUERY_FAILED"
    LINEAGE_STORE_FAILED = "LINEAGE_STORE_FAILED"

    # Audit errors (Sprint 9, Story 8.4)
    AUDIT_INTEGRITY_FAILED = "AUDIT_INTEGRITY_FAILED"
    AUDIT_QUERY_FAILED = "AUDIT_QUERY_FAILED"
    AUDIT_STORE_FAILED = "AUDIT_STORE_FAILED"

    # NeMo Curator errors (Sprint 9, Story 8.5)
    QUALITY_NEMO_MODEL_ERROR = "QUALITY_NEMO_MODEL_ERROR"

    # Dedup errors (Story 4.7)
    DEDUP_HASH_COMPUTATION_FAILED = "DEDUP_HASH_COMPUTATION_FAILED"

    # Export errors (Story 5.9)
    EXPORT_FORMAT_NOT_SUPPORTED = "EXPORT_FORMAT_NOT_SUPPORTED"
    EXPORT_PATH_INVALID = "EXPORT_PATH_INVALID"
    EXPORT_WRITE_FAILED = "EXPORT_WRITE_FAILED"

    # Blob storage errors (v1.0 M1)
    BLOB_UPLOAD_FAILED = "BLOB_UPLOAD_FAILED"
    BLOB_DOWNLOAD_FAILED = "BLOB_DOWNLOAD_FAILED"
    BLOB_DELETE_FAILED = "BLOB_DELETE_FAILED"
    BLOB_NOT_FOUND = "BLOB_NOT_FOUND"
    BLOB_PRESIGN_FAILED = "BLOB_PRESIGN_FAILED"

    # DuckDB extension errors (v1.0 M0a)
    LANCE_EXTENSION_ERROR = "LANCE_EXTENSION_ERROR"
    LANCE_SCAN_FAILED = "LANCE_SCAN_FAILED"
    DUCKLAKE_EXTENSION_ERROR = "DUCKLAKE_EXTENSION_ERROR"

    # RAG errors (v1.0 M2)
    RAG_RETRIEVAL_FAILED = "RAG_RETRIEVAL_FAILED"
    RAG_GENERATION_FAILED = "RAG_GENERATION_FAILED"
    RAG_CONTEXT_TOO_LONG = "RAG_CONTEXT_TOO_LONG"
    RAG_PROVIDER_ERROR = "RAG_PROVIDER_ERROR"
    RAG_STREAM_ERROR = "RAG_STREAM_ERROR"
    RAG_TEMPLATE_NOT_FOUND = "RAG_TEMPLATE_NOT_FOUND"
    RAG_SESSION_NOT_FOUND = "RAG_SESSION_NOT_FOUND"

    # Knowledge Graph errors (v1.0 M3)
    KG_CONNECTION_FAILED = "KG_CONNECTION_FAILED"
    KG_SCHEMA_ERROR = "KG_SCHEMA_ERROR"
    KG_QUERY_FAILED = "KG_QUERY_FAILED"
    KG_TRAVERSAL_TIMEOUT = "KG_TRAVERSAL_TIMEOUT"
    KG_BUILD_FAILED = "KG_BUILD_FAILED"
    KG_EXTRACT_FAILED = "KG_EXTRACT_FAILED"
    KG_GRAPH_NOT_FOUND = "KG_GRAPH_NOT_FOUND"

    # Auth errors (v1.0 M4)
    AUTH_TOKEN_EXPIRED = "AUTH_TOKEN_EXPIRED"
    AUTH_INVALID_TOKEN = "AUTH_INVALID_TOKEN"
    AUTH_INSUFFICIENT_PERMISSIONS = "AUTH_INSUFFICIENT_PERMISSIONS"
    AUTH_API_KEY_ROTATION_REQUIRED = "AUTH_API_KEY_ROTATION_REQUIRED"

    # Document processing errors (v1.2)
    DOCUMENT_PARSE_FAILED = "DOCUMENT_PARSE_FAILED"
    DOCUMENT_OCR_FAILED = "DOCUMENT_OCR_FAILED"
    DOCUMENT_CHUNK_FAILED = "DOCUMENT_CHUNK_FAILED"
    DOCUMENT_UPLOAD_FAILED = "DOCUMENT_UPLOAD_FAILED"
    DOCUMENT_UNSUPPORTED_FORMAT = "DOCUMENT_UNSUPPORTED_FORMAT"
    DOCUMENT_TOO_LARGE = "DOCUMENT_TOO_LARGE"

    # Transform errors (Daft repositioning Sprint 1)
    TRANSFORM_OP_UNKNOWN = "TRANSFORM_OP_UNKNOWN"
    TRANSFORM_EXECUTION_FAILED = "TRANSFORM_EXECUTION_FAILED"

    # Concurrency / resource errors (v1.6.0 Phase 2)
    STORAGE_LOCK_TIMEOUT = "STORAGE_LOCK_TIMEOUT"
    CONCURRENT_MODIFICATION = "CONCURRENT_MODIFICATION"
    RESOURCE_EXHAUSTED = "RESOURCE_EXHAUSTED"

    # Transient / retryable errors (v1.6.0 Phase 2)
    TRANSIENT_NETWORK_ERROR = "TRANSIENT_NETWORK_ERROR"
    TRANSIENT_RATE_LIMITED = "TRANSIENT_RATE_LIMITED"

    # Consistency errors (v1.6.0 Phase 2)
    CACHE_STALE = "CACHE_STALE"
    METADATA_CONFLICT = "METADATA_CONFLICT"


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


class HttpError(ArrowLakeError):
    """Errors related to HTTP data fetching (Story 3.2)."""


class EmbeddingError(ArrowLakeError):
    """Errors related to text embedding generation (Stories 4.1, 4.3)."""


class QualityError(ArrowLakeError):
    """Errors related to data quality filtering and validation (Epic 4)."""


class WorkflowError(ArrowLakeError):
    """Errors related to workflow orchestration and step execution (Epic 6)."""


class BackupError(WorkflowError):
    """Errors related to backup and restore operations."""


class SchemaEvolutionError(CatalogError):
    """Errors related to schema evolution and migration."""


class DuckDBError(QueryError):
    """Errors from the DuckDB query engine."""


class ArgoError(WorkflowError):
    """Errors related to Argo Workflows deployment and management (Sprint 7)."""


class AuditError(ArrowLakeError):
    """Errors related to audit trail integrity and queries (Sprint 9, Story 8.4)."""


class RAGError(ArrowLakeError):
    """Errors related to RAG pipeline operations (M2)."""


class KGError(ArrowLakeError):
    """Errors related to knowledge graph operations (M3)."""


class DocumentError(ArrowLakeError):
    """Errors related to document processing, parsing, and ingestion (v1.2)."""


class ConcurrencyError(ArrowLakeError):
    """Concurrent operation conflicts (lock timeout, optimistic lock mismatch)."""


class TransientError(ArrowLakeError):
    """Retryable transient failures (network blip, rate limit 429, temporary resource shortage)."""


class ConsistencyError(ArrowLakeError):
    """Distributed state inconsistency (cache staleness, metadata conflict)."""
