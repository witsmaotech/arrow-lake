"""Exception-to-HTTP response mapping."""

from __future__ import annotations

from typing import Any

from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

from arrow_lake.exceptions import ArrowLakeError, ErrorCode


def _scrub_bytes(obj: Any) -> Any:
    """Recursively replace ``bytes``/``bytearray`` with a placeholder.

    FastAPI's default ``request_validation_exception_handler`` feeds
    ``exc.errors()`` (which embeds the raw request ``input``) through
    ``jsonable_encoder``. Its ``bytes`` encoder is ``lambda o: o.decode()``
    (UTF-8), which raises ``UnicodeDecodeError`` on binary request bodies
    (PDF/image uploads, multipart boundaries) and turns a legitimate 422
    validation error into an opaque 500 INTERNAL_ERROR. Scrubbing first keeps
    the 422 and surfaces the real validation reason. Mirrors
    ``arrow_lake.api.models.common._json_safe_row``.
    """
    if isinstance(obj, (bytes, bytearray)):
        return f"<binary {len(obj)} bytes>"
    if isinstance(obj, dict):
        return {k: _scrub_bytes(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_scrub_bytes(x) for x in obj]
    return obj


def _safe_validation_errors(exc: Any) -> list[dict]:
    """422-safe view of ``exc.errors()`` (P0-4, 2026-08-21).

    Two hazards in the raw error list:
    1. ``input`` embeds the raw request value — reflecting it back echoes
       passwords and other submitted secrets into the response (and from
       there into client/proxy logs). Drop the key entirely: ``loc`` +
       ``msg`` + ``type`` are enough to debug the validation failure.
    2. ``input`` may be binary bytes, which crashes ``jsonable_encoder``
       (see ``_scrub_bytes``).
    """
    return [
        {k: v for k, v in err.items() if k != "input"}
        for err in _scrub_bytes(exc.errors())
    ]


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
    # RAG errors
    if code in (ErrorCode.RAG_TEMPLATE_NOT_FOUND, ErrorCode.RAG_SESSION_NOT_FOUND):
        return 404
    if code == ErrorCode.RAG_CONTEXT_TOO_LONG:
        return 400
    if code == ErrorCode.RAG_PROVIDER_ERROR:
        return 502
    # Knowledge Graph errors
    if code == ErrorCode.KG_GRAPH_NOT_FOUND:
        return 404
    if code == ErrorCode.KG_SCHEMA_ERROR:
        return 400
    if code == ErrorCode.KG_QUERY_FAILED:
        # Query-semantics failure (too-expensive traversal/OOM, unsupported
        # algorithm, NoIndex, ...) — a well-formed request the server cannot
        # satisfy. Not a server fault, so 422 not 500.
        return 422
    if code == ErrorCode.KG_TRAVERSAL_TIMEOUT:
        return 504
    if code == ErrorCode.KG_CONNECTION_FAILED or code == ErrorCode.KG_EXTRACT_FAILED:
        return 502
    if code.startswith("KG_"):
        return 500
    # Auth errors (M4)
    if code in (ErrorCode.AUTH_TOKEN_EXPIRED, ErrorCode.AUTH_INVALID_TOKEN):
        return 401
    if code == ErrorCode.AUTH_INSUFFICIENT_PERMISSIONS:
        return 403
    if code == ErrorCode.AUTH_API_KEY_ROTATION_REQUIRED:
        return 403
    # Document pipeline errors
    if code == ErrorCode.DOCUMENT_UNSUPPORTED_FORMAT:
        return 400
    if code == ErrorCode.DOCUMENT_TOO_LARGE:
        return 413
    if code in (
        ErrorCode.DOCUMENT_PARSE_FAILED,
        ErrorCode.DOCUMENT_OCR_FAILED,
        ErrorCode.DOCUMENT_CHUNK_FAILED,
        ErrorCode.DOCUMENT_UPLOAD_FAILED,
        ErrorCode.TRANSFORM_OP_UNKNOWN,
        ErrorCode.TRANSFORM_EXECUTION_FAILED,
        ErrorCode.QUALITY_NEMO_MODEL_ERROR,
    ):
        return 422
    return 500


def register_exception_handlers(app) -> None:
    """Register ArrowLakeError exception handler on a FastAPI app."""

    # Keys that may contain internal details and should not be exposed.
    _sensitive_context_keys = frozenset({"query", "host", "port", "sql", "file_path"})

    @app.exception_handler(ArrowLakeError)
    async def arrow_lake_error_handler(request: Request, exc: ArrowLakeError):
        status = _error_code_to_http_status(exc.error_code)
        safe_context: dict[str, Any] = {}
        if exc.context:
            safe_context = {
                k: v for k, v in exc.context.items()
                if k not in _sensitive_context_keys
            }
        return JSONResponse(
            status_code=status,
            content={
                "success": False,
                "error": exc.error_code.value,
                "message": exc.message,
                **({"context": safe_context} if safe_context else {}),
            },
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_error_handler(request: Request, exc: RequestValidationError):
        safe_errors = _safe_validation_errors(exc)
        return JSONResponse(
            status_code=422,
            content={
                "success": False,
                "error": "VALIDATION_ERROR",
                "message": "Request validation failed",
                "detail": jsonable_encoder(safe_errors),
            },
        )
