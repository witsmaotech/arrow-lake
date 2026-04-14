"""Tests for arrow_lake.exceptions — Story 1.4."""

import pytest
from arrow_lake.exceptions import (
    ArrowLakeError,
    CatalogError,
    EmbeddingError,
    ErrorCode,
    HttpError,
    IngestError,
    QueryError,
    RayRuntimeError,
    StorageError,
    ValidationError,
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

    def test_subclass_str_includes_class_info(self) -> None:
        error = StorageError(
            error_code=ErrorCode.STORAGE_CONNECTION_FAILED,
            message="Cannot connect to MinIO",
        )
        text = str(error)
        assert "Cannot connect to MinIO" in text
