"""SQL database connector — Daft Phase 2, Sprint 5.

Wraps ``daft.read_sql()`` to read from PostgreSQL, MySQL, SQLite,
ClickHouse and other SQLAlchemy-compatible databases directly into a
Daft DataFrame, then into Lance.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from typing import Any
from urllib.parse import urlparse

import daft

from arrow_lake.exceptions import ErrorCode, IngestError

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
    return any(addr in net for net in _PRIVATE_NETWORKS)


def _validate_connection_url(connection_url: str) -> None:
    """Block SSRF: reject connection URLs pointing to private/internal hosts."""
    try:
        parsed = urlparse(connection_url)
    except Exception as exc:
        raise IngestError(
            error_code=ErrorCode.VALIDATION_INVALID_CONFIG,
            message=f"Invalid connection_url: {exc}",
        ) from exc

    hostname = parsed.hostname
    if not hostname:
        raise IngestError(
            error_code=ErrorCode.VALIDATION_INVALID_CONFIG,
            message="connection_url must contain a hostname",
        )

    try:
        addr = ipaddress.ip_address(hostname)
        if _is_private_ip(addr):
            raise IngestError(
                error_code=ErrorCode.VALIDATION_INVALID_CONFIG,
                message="connection_url hostname resolves to a private/internal IP address",
            )
    except ValueError:
        try:
            addrs = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
            for _family, _, _, _, sockaddr in addrs:
                ip = ipaddress.ip_address(sockaddr[0])
                if _is_private_ip(ip):
                    raise IngestError(
                        error_code=ErrorCode.VALIDATION_INVALID_CONFIG,
                        message=f"connection_url hostname '{hostname}' resolves to private IP {ip}",
                    )
        except (socket.gaierror, OSError) as exc:
            raise IngestError(
                error_code=ErrorCode.VALIDATION_INVALID_CONFIG,
                message=f"Cannot resolve connection_url hostname '{hostname}': {exc}",
            ) from exc

_FORBIDDEN_SQL_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE)\b",
    re.IGNORECASE,
)


class SqlConnector:
    """Read data from a SQL database via Daft.

    Args:
        connection_url: SQLAlchemy-style connection string or callable.
        partition_col: Optional column for parallel partitioned reads.
        num_partitions: Number of read partitions (requires partition_col).
    """

    def __init__(
        self,
        connection_url: str,
        *,
        partition_col: str | None = None,
        num_partitions: int | None = None,
    ) -> None:
        _validate_connection_url(connection_url)
        self._conn = connection_url
        self._partition_col = partition_col
        self._num_partitions = num_partitions

    def read(self, sql: str) -> daft.DataFrame:
        """Execute a SQL query and return a Daft DataFrame.

        Args:
            sql: SELECT query to execute.

        Returns:
            Daft DataFrame with query results.

        Raises:
            IngestError: If SQL contains forbidden statements or execution fails.
        """
        _validate_sql_readonly(sql)
        try:
            kwargs: dict[str, Any] = {}
            if self._partition_col:
                kwargs["partition_col"] = self._partition_col
            if self._num_partitions:
                kwargs["num_partitions"] = self._num_partitions
            return daft.read_sql(sql, self._conn, **kwargs)
        except Exception as exc:
            raise IngestError(
                error_code=ErrorCode.INGEST_FILE_NOT_FOUND,
                message=f"SQL read failed: {exc}",
                context={"sql": sql[:200], "connection_url": self._safe_url()},
            ) from exc

    def _safe_url(self) -> str:
        """Mask credentials in connection URL for logging."""
        return re.sub(r"://([^:]+):([^@]+)@", r"://***:***@", self._conn)


def _validate_sql_readonly(sql: str) -> None:
    """Reject non-SELECT statements to prevent mutation via ingest."""
    stripped = sql.strip().upper()
    if not stripped.startswith("SELECT") and not stripped.startswith("WITH"):
        raise IngestError(
            error_code=ErrorCode.VALIDATION_INVALID_CONFIG,
            message="Only SELECT queries are allowed for SQL ingestion",
            context={"sql_prefix": sql[:100]},
        )
    if _FORBIDDEN_SQL_RE.search(sql):
        raise IngestError(
            error_code=ErrorCode.VALIDATION_INVALID_CONFIG,
            message="SQL contains forbidden mutation statements",
            context={"sql_prefix": sql[:100]},
        )
