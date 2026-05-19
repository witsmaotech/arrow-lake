"""HTTP file connector — Story 3.2.

Fetches files from HTTP(S) URLs with retry and error mapping.
Implements the FileConnector protocol from connectors.py.
"""

from __future__ import annotations

import contextlib
import ipaddress
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from arrow_lake.exceptions import ErrorCode, HttpError
from arrow_lake.ingest.connectors import ConnectorResult

# Private IP ranges that should be blocked (SSRF prevention)
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("::ffff:0.0.0.0/96"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def _is_private_ip(addr: ipaddress._BaseAddress) -> bool:
    """Check if an IP address falls in any private/reserved range."""
    return any(addr in net for net in _PRIVATE_NETWORKS)


def _is_safe_hostname(hostname: str) -> bool:
    """Check if hostname is not a private/internal IP address.

    Resolves domain names via DNS to prevent DNS rebinding attacks.
    """
    import socket

    try:
        addr = ipaddress.ip_address(hostname)
        return not _is_private_ip(addr)
    except ValueError:
        # Not an IP literal — resolve DNS and check resolved IPs
        try:
            addrs = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
            for _family, _, _, _, sockaddr in addrs:
                ip = ipaddress.ip_address(sockaddr[0])
                if _is_private_ip(ip):
                    return False
        except (socket.gaierror, OSError):
            pass
        return True


@dataclass(frozen=True)
class HttpFetchResult:
    """Result of an HTTP fetch operation."""

    url: str
    content: bytes
    content_type: str
    status_code: int


class _SSRFSafeTransport(httpx.HTTPTransport):
    """Transport that validates the resolved IP after connection to prevent DNS rebinding."""

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        resp = super().handle_request(request)
        stream = getattr(resp, 'stream', None)
        if stream is not None:
            conn = getattr(stream, '_connection', None)
            if conn is not None:
                sock = getattr(conn, 'sock', None) or getattr(conn, '_sock', None)
                if sock is not None and hasattr(sock, 'getpeername'):
                    addr = sock.getpeername()[0]
                    try:
                        if _is_private_ip(ipaddress.ip_address(addr)):
                            resp.close()
                            raise HttpError(
                                error_code=ErrorCode.HTTP_FETCH_FAILED,
                                message=f"Connection to private IP blocked (DNS rebinding): {addr}",
                            )
                    except ValueError:
                        pass
        return resp


class HttpConnector:
    """Fetches files from HTTP(S) URLs.

    Features:
    - URL scheme validation (only http/https) for SSRF prevention
    - DNS-rebinding protection via post-connection IP validation
    - Retry with exponential backoff on transient errors
    - Error mapping: 4xx→HTTP_FETCH_FAILED, 408/504→HTTP_TIMEOUT, 429→HTTP_RATE_LIMITED

    Args:
        timeout_seconds: Request timeout in seconds.
        max_retries: Maximum retry attempts for transient failures.
    """

    def __init__(
        self,
        timeout_seconds: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        from arrow_lake.core.http import create_http_client

        self._client = create_http_client(
            transport=_SSRFSafeTransport(),
            timeout=timeout_seconds,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self._client.close()

    def __enter__(self) -> HttpConnector:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _validate_url(self, url: str) -> bool:
        """Validate URL scheme and block private/internal IP addresses.

        Args:
            url: URL to validate.

        Returns:
            True if valid.

        Raises:
            HttpError: If URL scheme is not http/https or hostname resolves
                         to a private IP range.
        """
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise HttpError(
                error_code=ErrorCode.HTTP_FETCH_FAILED,
                message=f"URL scheme '{parsed.scheme}' not allowed (only http/https): {url}",
            )

        hostname = parsed.hostname
        if hostname and not _is_safe_hostname(hostname):
            raise HttpError(
                error_code=ErrorCode.HTTP_FETCH_FAILED,
                message=f"URL hostname '{hostname}' resolves to a private IP range: {url}",
            )

        return True

    @staticmethod
    def _map_status_code(status_code: int) -> ErrorCode | None:
        """Map HTTP status code to ErrorCode.

        Args:
            status_code: HTTP response status code.

        Returns:
            ErrorCode for error statuses, None for success.
        """
        if 200 <= status_code < 300:
            return None
        if status_code == 429:
            return ErrorCode.HTTP_RATE_LIMITED
        if status_code in (408, 504):
            return ErrorCode.HTTP_TIMEOUT
        return ErrorCode.HTTP_FETCH_FAILED

    def _build_retry_decorator(self) -> Any:
        """Build tenacity retry decorator with configured settings."""
        return retry(
            retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
            stop=stop_after_attempt(self.max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            reraise=True,
        )

    def fetch(self, url: str) -> HttpFetchResult:
        """Fetch a file from an HTTP(S) URL.

        Args:
            url: HTTP(S) URL to fetch.

        Returns:
            HttpFetchResult with content and metadata.

        Raises:
            HttpError: On validation error, client error, or timeout.
        """
        self._validate_url(url)

        retry_decorator = self._build_retry_decorator()

        def _do_fetch() -> HttpFetchResult:
            response = self._client.get(url)
            error_code = self._map_status_code(response.status_code)
            if error_code is not None:
                raise HttpError(
                    error_code=error_code,
                    message=f"HTTP {response.status_code} fetching {url}: "
                    f"{response.text[:200]}",
                )
            return HttpFetchResult(
                url=url,
                content=response.content,
                content_type=response.headers.get("content-type", ""),
                status_code=response.status_code,
            )

        result: HttpFetchResult = retry_decorator(_do_fetch)()
        return result

    def list_files(
        self,
        extensions: list[str] | None = None,
    ) -> ConnectorResult:
        """List files — returns empty result for HTTP connector.

        HTTP connector uses fetch() for individual URLs rather
        than file discovery. This satisfies the FileConnector protocol.

        Args:
            extensions: Ignored for HTTP connector.

        Returns:
            Empty ConnectorResult.
        """
        return ConnectorResult(paths=(), file_count=0)
