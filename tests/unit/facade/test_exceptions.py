"""Tests for arrow_lake.exceptions — Story 1.4."""

import pytest
from arrow_lake.exceptions import (
    ArrowLakeError,
    CatalogError,
    EmbeddingError,
    ErrorCode,
    HttpError,
    IngestError,
    QualityError,
    QueryError,
    RayRuntimeError,
    StorageError,
    ValidationError,
    WorkflowError,
)


class TestErrorCode:
    """Test ErrorCode enum values."""

    def test_enum_has_expected_categories(self) -> None:
        expected_categories = {
            "STORAGE",
            "QUERY",
            "INGEST",
            "CATALOG",
            "RAY",
            "VALIDATION",
            "HTTP",
            "IMAGE",
            "VIDEO",
            "SCENE",
            "EMBEDDING",
            "VECTOR",
            "FTS",
            "HYBRID",
            "OLAP",
            "QUALITY",
            "WORKFLOW",
            "ARGO",
            "AUTOSCALE",
            "LIFECYCLE",
            "FACET",
            "ENSEMBLE",
            "LINEAGE",
            "AUDIT",
            "DEDUP",
            "EXPORT",
            "BLOB",
            "LANCE",
            "DUCKLAKE",
            "RAG",
            "KG",
            "AUTH",
            "DOCUMENT",
        }
        actual_categories = {code.name.split("_")[0] for code in ErrorCode}
        assert actual_categories == expected_categories

    def test_each_error_code_is_string(self) -> None:
        for code in ErrorCode:
            assert isinstance(code.value, str)
            assert len(code.value) > 0


class TestArrowLakeError:
    """Test base ArrowLakeError."""

    def test_base_error_has_required_attributes(self) -> None:
        error = ArrowLakeError(
            error_code=ErrorCode.STORAGE_CONNECTION_FAILED,
            message="Connection failed",
            context={"host": "localhost"},
        )
        assert error.error_code == ErrorCode.STORAGE_CONNECTION_FAILED
        assert error.message == "Connection failed"
        assert error.context == {"host": "localhost"}

    def test_base_error_str_representation(self) -> None:
        error = ArrowLakeError(
            error_code=ErrorCode.STORAGE_CONNECTION_FAILED,
            message="Connection failed",
        )
        text = str(error)
        assert "Connection failed" in text
        assert "STORAGE_CONNECTION_FAILED" in text

    def test_base_error_is_exception(self) -> None:
        with pytest.raises(ArrowLakeError):
            raise ArrowLakeError(
                error_code=ErrorCode.STORAGE_CONNECTION_FAILED,
                message="test",
            )

    def test_base_error_default_context_is_empty(self) -> None:
        error = ArrowLakeError(
            error_code=ErrorCode.STORAGE_CONNECTION_FAILED,
            message="test",
        )
        assert error.context == {}

    def test_base_error_preserves_message_attribute(self) -> None:
        error = ArrowLakeError(
            error_code=ErrorCode.QUERY_TIMEOUT,
            message="Query timed out after 30s",
        )
        assert error.message == "Query timed out after 30s"


class TestSubclassErrors:
    """Test all subclass exceptions inherit correctly."""

    @pytest.mark.parametrize(
        "exception_cls,error_code",
        [
            (StorageError, ErrorCode.STORAGE_CONNECTION_FAILED),
            (QueryError, ErrorCode.QUERY_TIMEOUT),
            (IngestError, ErrorCode.INGEST_SCHEMA_MISMATCH),
            (CatalogError, ErrorCode.CATALOG_DATASET_NOT_FOUND),
            (RayRuntimeError, ErrorCode.RAY_RUNTIME_ACTOR_DEAD),
            (ValidationError, ErrorCode.VALIDATION_INVALID_CONFIG),
            (HttpError, ErrorCode.HTTP_FETCH_FAILED),
            (EmbeddingError, ErrorCode.EMBEDDING_MODEL_ERROR),
            (QualityError, ErrorCode.QUALITY_FILTER_EXECUTION_ERROR),
            (WorkflowError, ErrorCode.WORKFLOW_STEP_FAILED),
        ],
    )
    def test_subclass_is_arrow_lake_error(self, exception_cls: type, error_code: ErrorCode) -> None:
        error = exception_cls(error_code=error_code, message="test")
        assert isinstance(error, ArrowLakeError)
        assert isinstance(error, Exception)

    @pytest.mark.parametrize(
        "exception_cls",
        [
            StorageError,
            QueryError,
            IngestError,
            CatalogError,
            RayRuntimeError,
            ValidationError,
            HttpError,
            EmbeddingError,
            QualityError,
        ],
    )
    def test_subclass_can_be_raised_and_caught(self, exception_cls: type) -> None:
        with pytest.raises(ArrowLakeError):
            raise exception_cls(
                error_code=ErrorCode.STORAGE_CONNECTION_FAILED,
                message="test",
            )

    @pytest.mark.parametrize(
        "exception_cls",
        [
            StorageError,
            QueryError,
            IngestError,
            CatalogError,
            RayRuntimeError,
            ValidationError,
            HttpError,
            EmbeddingError,
            QualityError,
            WorkflowError,
        ],
    )
    def test_subclass_preserves_attributes(self, exception_cls: type) -> None:
        error = exception_cls(
            error_code=ErrorCode.STORAGE_CONNECTION_FAILED,
            message="test message",
            context={"key": "value"},
        )
        assert error.error_code == ErrorCode.STORAGE_CONNECTION_FAILED
        assert error.message == "test message"
        assert error.context == {"key": "value"}

    def test_subclass_catches_correctly(self) -> None:
        with pytest.raises(StorageError):
            raise StorageError(
                error_code=ErrorCode.STORAGE_CONNECTION_FAILED,
                message="test",
            )

        # StorageError is also an ArrowLakeError
        try:
            raise StorageError(
                error_code=ErrorCode.STORAGE_CONNECTION_FAILED,
                message="test",
            )
        except ArrowLakeError:
            pass

        # QueryError is also an ArrowLakeError but not a StorageError
        try:
            raise QueryError(
                error_code=ErrorCode.QUERY_TIMEOUT,
                message="test",
            )
        except ArrowLakeError:
            pass
        except StorageError:
            pytest.fail("QueryError should not be caught as StorageError")

        # WorkflowError is also an ArrowLakeError
        try:
            raise WorkflowError(
                error_code=ErrorCode.WORKFLOW_STEP_FAILED,
                message="test",
            )
        except ArrowLakeError:
            pass
        except StorageError:
            pytest.fail("WorkflowError should not be caught as StorageError")

    def test_subclass_str_includes_class_info(self) -> None:
        error = StorageError(
            error_code=ErrorCode.STORAGE_CONNECTION_FAILED,
            message="Cannot connect to MinIO",
        )
        text = str(error)
        assert "Cannot connect to MinIO" in text


class TestVectorErrorCodes:
    """Test vector search error codes (Story 5.1)."""

    def test_vector_index_failed_exists(self) -> None:
        code = ErrorCode.VECTOR_INDEX_FAILED
        assert isinstance(code.value, str)
        assert "INDEX" in code.name

    def test_vector_search_failed_exists(self) -> None:
        code = ErrorCode.VECTOR_SEARCH_FAILED
        assert isinstance(code.value, str)
        assert "SEARCH" in code.name

    def test_vector_dimension_mismatch_exists(self) -> None:
        code = ErrorCode.VECTOR_DIMENSION_MISMATCH
        assert isinstance(code.value, str)
        assert "DIMENSION" in code.name

    def test_vector_index_too_few_rows_exists(self) -> None:
        code = ErrorCode.VECTOR_INDEX_TOO_FEW_ROWS
        assert isinstance(code.value, str)
        assert "TOO_FEW_ROWS" in code.name

    def test_vector_invalid_query_exists(self) -> None:
        code = ErrorCode.VECTOR_INVALID_QUERY
        assert isinstance(code.value, str)
        assert "INVALID_QUERY" in code.name

    def test_vector_errors_use_query_error(self) -> None:
        """All vector error codes should be raised as QueryError."""
        for code in [
            ErrorCode.VECTOR_INDEX_FAILED,
            ErrorCode.VECTOR_SEARCH_FAILED,
            ErrorCode.VECTOR_DIMENSION_MISMATCH,
            ErrorCode.VECTOR_INDEX_TOO_FEW_ROWS,
            ErrorCode.VECTOR_INVALID_QUERY,
        ]:
            error = QueryError(error_code=code, message="test")
            assert isinstance(error, ArrowLakeError)


class TestFTSErrorCodes:
    """Test full-text search error codes (Story 5.2)."""

    def test_fts_index_failed_exists(self) -> None:
        code = ErrorCode.FTS_INDEX_FAILED
        assert isinstance(code.value, str)
        assert "INDEX" in code.name

    def test_fts_search_failed_exists(self) -> None:
        code = ErrorCode.FTS_SEARCH_FAILED
        assert isinstance(code.value, str)
        assert "SEARCH" in code.name

    def test_fts_errors_use_query_error(self) -> None:
        """All FTS error codes should be raised as QueryError."""
        for code in [ErrorCode.FTS_INDEX_FAILED, ErrorCode.FTS_SEARCH_FAILED]:
            error = QueryError(error_code=code, message="test")
            assert isinstance(error, ArrowLakeError)


class TestHybridErrorCodes:
    """Test hybrid search error codes (Story 5.3)."""

    def test_hybrid_search_failed_exists(self) -> None:
        code = ErrorCode.HYBRID_SEARCH_FAILED
        assert isinstance(code.value, str)
        assert "SEARCH" in code.name

    def test_hybrid_errors_use_query_error(self) -> None:
        """All hybrid error codes should be raised as QueryError."""
        error = QueryError(error_code=ErrorCode.HYBRID_SEARCH_FAILED, message="test")
        assert isinstance(error, ArrowLakeError)


class TestOlapErrorCodes:
    """Test OLAP analytics error codes (Story 5.4)."""

    def test_olap_query_failed_exists(self) -> None:
        code = ErrorCode.OLAP_QUERY_FAILED
        assert isinstance(code.value, str)
        assert "QUERY" in code.name

    def test_olap_errors_use_query_error(self) -> None:
        """All OLAP error codes should be raised as QueryError."""
        error = QueryError(error_code=ErrorCode.OLAP_QUERY_FAILED, message="test")
        assert isinstance(error, ArrowLakeError)


class TestQualityErrorCodes:
    """Test quality error codes (Epic 4)."""

    def test_all_quality_error_codes_exist(self) -> None:
        expected = {
            ErrorCode.QUALITY_FILTER_EXECUTION_ERROR,
            ErrorCode.QUALITY_SCHEMA_UNKNOWN_COLUMN,
            ErrorCode.QUALITY_SCHEMA_TYPE_MISMATCH,
            ErrorCode.QUALITY_EMBEDDING_IMAGE_FAILED,
            ErrorCode.QUALITY_DEAD_LETTER_WRITE_FAILED,
        }
        assert expected <= set(ErrorCode)

    def test_quality_error_codes_have_quality_prefix(self) -> None:
        for code in [
            ErrorCode.QUALITY_FILTER_EXECUTION_ERROR,
            ErrorCode.QUALITY_SCHEMA_UNKNOWN_COLUMN,
            ErrorCode.QUALITY_SCHEMA_TYPE_MISMATCH,
            ErrorCode.QUALITY_EMBEDDING_IMAGE_FAILED,
            ErrorCode.QUALITY_DEAD_LETTER_WRITE_FAILED,
        ]:
            assert code.name.startswith("QUALITY_")

    def test_quality_errors_use_quality_error(self) -> None:
        """All quality error codes should be raised as QualityError."""
        for code in [
            ErrorCode.QUALITY_FILTER_EXECUTION_ERROR,
            ErrorCode.QUALITY_SCHEMA_UNKNOWN_COLUMN,
            ErrorCode.QUALITY_SCHEMA_TYPE_MISMATCH,
            ErrorCode.QUALITY_EMBEDDING_IMAGE_FAILED,
            ErrorCode.QUALITY_DEAD_LETTER_WRITE_FAILED,
        ]:
            error = QualityError(error_code=code, message="test")
            assert isinstance(error, ArrowLakeError)
            assert isinstance(error, Exception)

    def test_quality_error_inherits_from_arrow_lake_error(self) -> None:
        error = QualityError(
            error_code=ErrorCode.QUALITY_FILTER_EXECUTION_ERROR,
            message="filter failed",
            context={"filter": "text_length"},
        )
        assert isinstance(error, ArrowLakeError)
        assert error.error_code == ErrorCode.QUALITY_FILTER_EXECUTION_ERROR
        assert error.message == "filter failed"
        assert error.context == {"filter": "text_length"}


class TestWorkflowErrorCodes:
    """Test workflow error codes (Epic 6)."""

    def test_all_workflow_error_codes_exist(self) -> None:
        expected = {
            ErrorCode.WORKFLOW_STEP_FAILED,
            ErrorCode.WORKFLOW_RETRY_EXHAUSTED,
            ErrorCode.WORKFLOW_STATE_ROLLBACK_FAILED,
            ErrorCode.WORKFLOW_SCHEDULING_FAILED,
            ErrorCode.WORKFLOW_TAG_CONFLICT,
            ErrorCode.WORKFLOW_RESUME_FAILED,
        }
        assert expected <= set(ErrorCode)

    def test_workflow_error_codes_have_workflow_prefix(self) -> None:
        for code in [
            ErrorCode.WORKFLOW_STEP_FAILED,
            ErrorCode.WORKFLOW_RETRY_EXHAUSTED,
            ErrorCode.WORKFLOW_STATE_ROLLBACK_FAILED,
            ErrorCode.WORKFLOW_SCHEDULING_FAILED,
            ErrorCode.WORKFLOW_TAG_CONFLICT,
            ErrorCode.WORKFLOW_RESUME_FAILED,
        ]:
            assert code.name.startswith("WORKFLOW_")

    def test_workflow_errors_use_workflow_error(self) -> None:
        """All workflow error codes should be raised as WorkflowError."""
        for code in [
            ErrorCode.WORKFLOW_STEP_FAILED,
            ErrorCode.WORKFLOW_RETRY_EXHAUSTED,
            ErrorCode.WORKFLOW_STATE_ROLLBACK_FAILED,
            ErrorCode.WORKFLOW_SCHEDULING_FAILED,
            ErrorCode.WORKFLOW_TAG_CONFLICT,
            ErrorCode.WORKFLOW_RESUME_FAILED,
        ]:
            error = WorkflowError(error_code=code, message="test")
            assert isinstance(error, ArrowLakeError)
            assert isinstance(error, Exception)

    def test_workflow_error_inherits_from_arrow_lake_error(self) -> None:
        error = WorkflowError(
            error_code=ErrorCode.WORKFLOW_STEP_FAILED,
            message="step failed",
            context={"step": "validate_schema"},
        )
        assert isinstance(error, ArrowLakeError)
        assert error.error_code == ErrorCode.WORKFLOW_STEP_FAILED
        assert error.message == "step failed"
        assert error.context == {"step": "validate_schema"}
