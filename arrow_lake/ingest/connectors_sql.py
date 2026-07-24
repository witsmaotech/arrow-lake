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

    def fetch_column_comments(self, sql: str) -> dict[str, str]:
        """Best-effort column-comment capture for the query's source table.

        Resolves the dialect from ``connection_url`` and queries the catalog:
          - MySQL: ``information_schema.columns.column_comment``
          - PostgreSQL: ``col_description(table_oid, attnum)``
        Other dialects return ``{}``. The first ``FROM <table>`` in ``sql`` is
        used (qualified ``schema.table`` supported). Any failure returns ``{}``
        — comment capture must never block ingestion.
        """
        scheme = (urlparse(self._conn).scheme or "").lower()
        if scheme.startswith("mysql"):
            dialect = "mysql"
        elif scheme.startswith("postgres") or scheme in ("postgresql",):
            dialect = "postgres"
        else:
            return {}

        match = re.search(r"\bFROM\s+([A-Za-z_][\w.]*)", sql, re.IGNORECASE)
        if not match:
            return {}
        parts = match.group(1).split(".")
        table = parts[-1].strip('"').strip("`")
        schema = parts[-2].strip('"').strip("`") if len(parts) >= 2 else None
        if not table:
            return {}

        if dialect == "mysql":
            stmt = (
                "SELECT column_name, column_comment "
                "FROM information_schema.columns "
                "WHERE table_name = :t"
            )
            params: dict[str, str] = {"t": table}
            if schema:
                stmt += " AND table_schema = :s"
                params["s"] = schema
        else:  # postgres
            stmt = (
                "SELECT a.attname AS col, "
                "col_description(a.attrelid, a.attnum) AS cmt "
                "FROM pg_attribute a "
                "JOIN pg_class c ON a.attrelid = c.oid "
                "JOIN pg_namespace n ON c.relnamespace = n.oid "
                "WHERE c.relname = :t AND a.attnum > 0 AND NOT a.attisdropped"
            )
            params = {"t": table}
            if schema:
                stmt += " AND n.nspname = :s"
                params["s"] = schema

        try:
            from sqlalchemy import create_engine, text
        except Exception:
            return {}
        try:
            engine = create_engine(self._conn)
            with engine.connect() as conn:
                rows = conn.execute(text(stmt), params).fetchall()
        except Exception:
            return {}
        finally:
            try:
                engine.dispose()
            except Exception:
                pass

        out: dict[str, str] = {}
        for col_name, comment in rows:
            if comment and str(comment).strip():
                out[str(col_name)] = str(comment).strip()
        return out


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
