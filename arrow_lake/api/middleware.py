"""Request/response middleware for compression and size limits."""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests whose body exceeds a configured maximum size."""

    def __init__(self, app, max_size_bytes: int = 100 * 1024 * 1024) -> None:
        super().__init__(app)
        self.max_size_bytes = max_size_bytes

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint):
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                size = int(content_length)
                if size > self.max_size_bytes:
                    from starlette.responses import JSONResponse

                    return JSONResponse(
                        status_code=413,
                        content={
                            "success": False,
                            "error": "REQUEST_TOO_LARGE",
                            "message": (
                                f"Request body ({size} bytes) exceeds "
                                f"maximum allowed size ({self.max_size_bytes} bytes)"
                            ),
                        },
                    )
            except (ValueError, TypeError):
                pass

        return await call_next(request)
