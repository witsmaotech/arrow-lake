"""Exception-to-HTTP response mapping."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

from arrow_lake.exceptions import ArrowLakeError, ErrorCode


def _error_code_to_http_status(code: ErrorCode) -> int:
    """Map an ArrowLake ErrorCode to an HTTP status code.

    Rules:
    - VALIDATION_* / QUERY_SYNTAX_ERROR / INGEST_SCHEMA_MISMATCH → 400
    - CATALOG_DATASET_NOT_FOUND / QUERY_INDEX_NOT_FOUND         → 404
    - CATALOG_DATASET_ALREADY_EXISTS                            → 409
    - CATALOG_RATE_LIMITED                                       → 429
    - QUERY_TIMEOUT                                             → 504
    - RAY_RUNTIME_*                                             → 503
    - STORAGE_* / HTTP_* / EMBEDDING_*                          → 502
    - Everything else                                            → 500
    """
    if code.startswith("VALIDATION_") or code == ErrorCode.QUERY_SYNTAX_ERROR:
        return 400
    if code == ErrorCode.INGEST_SCHEMA_MISMATCH or code == ErrorCode.INGEST_UNSUPPORTED_FORMAT:
        return 400
    if code == ErrorCode.INGEST_FILE_NOT_FOUND:
        return 400
    if code in (
        ErrorCode.CATALOG_DATASET_NOT_FOUND,
        ErrorCode.QUERY_INDEX_NOT_FOUND,
        ErrorCode.QUERY_TABLE_NOT_REGISTERED,
        ErrorCode.WORKFLOW_FLOW_NOT_FOUND,
        ErrorCode.STORAGE_PATH_NOT_FOUND,
    ):
        return 404
    if code == ErrorCode.CATALOG_DATASET_ALREADY_EXISTS:
        return 409
    if code == ErrorCode.CATALOG_RATE_LIMITED or code == ErrorCode.HTTP_RATE_LIMITED:
        return 429
    if code == ErrorCode.QUERY_TIMEOUT:
        return 504
    if code.startswith("RAY_RUNTIME_"):
        return 503
    if code.startswith("STORAGE_") or code.startswith("HTTP_") or code.startswith("EMBEDDING_"):
        return 502
    return 500


def register_exception_handlers(app) -> None:
    """Register ArrowLakeError exception handler on a FastAPI app."""

    @app.exception_handler(ArrowLakeError)
    async def arrow_lake_error_handler(request: Request, exc: ArrowLakeError):
        status = _error_code_to_http_status(exc.error_code)
        return JSONResponse(
            status_code=status,
            content={
                "success": False,
                "error": exc.error_code.value,
                "message": exc.message,
                **({"context": exc.context} if exc.context else {}),
            },
        )
