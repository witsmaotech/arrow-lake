"""TurboOCR HTTP client for scanned document OCR processing.

Provides TurboOcrClient with:
- HTTP-based OCR request/response
- Health check endpoint
- Retry with exponential backoff
- Circuit breaker for service unavailability
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

from arrow_lake.exceptions import DocumentError, ErrorCode

logger = logging.getLogger(__name__)

__all__ = ["TurboOcrClient", "TurboOcrResult"]


@dataclass(frozen=True)
class TurboOcrResult:
    """Result from TurboOCR processing.

    Attributes:
        text: Extracted text content.
        page_count: Number of pages processed.
        confidence: Average OCR confidence (0.0 - 1.0).
    """

    text: str
    page_count: int = 1
    confidence: float = 0.0


class _CircuitBreaker:
    """Thread-safe circuit breaker for OCR service calls."""

    def __init__(self, failure_threshold: int = 5, reset_timeout: float = 60.0) -> None:
        self._failure_threshold = failure_threshold
        self._reset_timeout = reset_timeout
        self._failure_count = 0
        self._last_failure: float = 0.0
        self._open = False
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            if not self._open:
                return True
            if time.monotonic() - self._last_failure > self._reset_timeout:
                self._open = False
                self._failure_count = 0
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            self._failure_count = 0
            self._open = False

    def record_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            self._last_failure = time.monotonic()
            if self._failure_count >= self._failure_threshold:
                self._open = True
                logger.warning("turbo_ocr_circuit_open failures=%d", self._failure_count)


class TurboOcrClient:
    """HTTP client for TurboOCR service.

    Args:
        endpoint: Base URL of the TurboOCR service.
        timeout: Request timeout in seconds.
        max_retries: Maximum retry attempts per request.
        retry_base_delay: Base delay for exponential backoff (seconds).
    """

    def __init__(
        self,
        endpoint: str = "http://localhost:8002",
        *,
        timeout: float = 300.0,
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
    ) -> None:
        self._validate_endpoint(endpoint)
        self._endpoint = endpoint.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay
        self._circuit = _CircuitBreaker()

    # Allowed local hostnames for development use (OCR endpoint, not user-facing).
    _LOCALHOST_ALIASES = frozenset({"localhost", "host.docker.internal"})

    @staticmethod
    def _validate_endpoint(url: str) -> None:
        """Reject endpoints pointing to private/internal IPs or non-HTTP schemes."""
        import ipaddress

        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"OCR endpoint must use http/https scheme, got: {parsed.scheme}")
        hostname = parsed.hostname
        if not hostname:
            raise ValueError("OCR endpoint must include a hostname")
        if hostname in TurboOcrClient._LOCALHOST_ALIASES:
            return
        private_ranges = [
            ipaddress.ip_network("127.0.0.0/8"),
            ipaddress.ip_network("10.0.0.0/8"),
            ipaddress.ip_network("172.16.0.0/12"),
            ipaddress.ip_network("192.168.0.0/16"),
            ipaddress.ip_network("169.254.0.0/16"),
            ipaddress.ip_network("0.0.0.0/8"),
            ipaddress.ip_network("::1/128"),
            ipaddress.ip_network("fc00::/7"),
            ipaddress.ip_network("fe80::/10"),
        ]
        try:
            addr = ipaddress.ip_address(hostname)
        except ValueError:
            return  # hostname is a domain, not an IP
        if any(addr in net for net in private_ranges):
            raise ValueError(f"OCR endpoint must not point to a private IP: {hostname}")

    @property
    def endpoint(self) -> str:
        return self._endpoint

    def is_available(self) -> bool:
        """Check if the TurboOCR service is healthy.

        Returns:
            True if the health endpoint responds successfully.
        """
        if not self._circuit.allow():
            return False
        try:
            resp = httpx.get(
                f"{self._endpoint}/health",
                timeout=5.0,
            )
            return resp.status_code == 200
        except (OSError, RuntimeError, httpx.HTTPError):
            return False

    def ocr(self, pdf_bytes: bytes, filename: str = "document.pdf") -> TurboOcrResult:
        """Send a PDF for OCR processing.

        Args:
            pdf_bytes: Raw PDF file bytes.
            filename: Original filename for logging.

        Returns:
            TurboOcrResult with extracted text and metadata.

        Raises:
            DocumentError: If OCR fails after retries or circuit is open.
        """
        if not self._circuit.allow():
            raise DocumentError(
                error_code=ErrorCode.DOCUMENT_OCR_FAILED,
                message="TurboOCR circuit breaker is open — service unavailable",
            )

        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                resp = httpx.post(
                    f"{self._endpoint}/ocr",
                    files={"file": (filename, pdf_bytes, "application/pdf")},
                    timeout=self._timeout,
                )
                resp.raise_for_status()
                data = resp.json()
                self._circuit.record_success()
                return TurboOcrResult(
                    text=data.get("text", ""),
                    page_count=data.get("page_count", 1),
                    confidence=data.get("confidence", 0.0),
                )
            except httpx.ConnectError as exc:
                last_error = exc
                logger.warning(
                    "turbo_ocr_connect_error attempt=%d filename=%s",
                    attempt + 1,
                    filename,
                )
            except httpx.TimeoutException as exc:
                last_error = exc
                logger.warning(
                    "turbo_ocr_timeout attempt=%d filename=%s",
                    attempt + 1,
                    filename,
                )
            except httpx.HTTPStatusError as exc:
                last_error = exc
                logger.warning(
                    "turbo_ocr_http_error status=%d attempt=%d",
                    exc.response.status_code,
                    attempt + 1,
                )
                break  # Don't retry HTTP errors

            # Exponential backoff
            delay = self._retry_base_delay * (2 ** attempt)
            time.sleep(delay)

        self._circuit.record_failure()
        raise DocumentError(
            error_code=ErrorCode.DOCUMENT_OCR_FAILED,
            message=f"TurboOCR failed after {self._max_retries} attempts: {last_error}",
            context={"filename": filename},
        ) from last_error
