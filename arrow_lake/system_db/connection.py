"""SystemDB — singleton libSQL connection for control-plane persistence.

Wraps the ``libsql`` Python SDK (sqlite3 DB-API style) with connect-retry,
health probe, and a process-local write lock. libSQL serializes writes, so
mutators go through :meth:`SystemDB.with_write` to avoid
``database is locked`` under concurrent threads within one worker.

Each worker process connects independently to the self-hosted sqld server
in production; embedded ``file:`` mode is used for dev and tests.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from typing import Any

import libsql
import structlog

logger = structlog.get_logger(__name__)


class SystemDBError(RuntimeError):
    """Raised when the system database is unavailable / unhealthy."""


class SystemDB:
    """Holder for a single libSQL connection.

    Args:
        url: ``file:local.db`` | ``http://host:port`` | ``:memory:``.
        auth_token: sqld bearer token (empty for embedded / no-auth server).
        connect_timeout_seconds: total budget for connect retries.
    """

    def __init__(
        self,
        url: str,
        *,
        auth_token: str = "",
        connect_timeout_seconds: float = 5.0,
    ) -> None:
        self._url = url
        # libsql.connect rejects None; empty string is the documented "no token".
        self._auth_token = auth_token or ""
        self._timeout = connect_timeout_seconds
        self._write_lock = threading.RLock()
        self._conn = self._connect_with_retry()

    @property
    def url(self) -> str:
        return self._url

    # ------------------------------------------------------------------
    def _connect_with_retry(self) -> Any:
        deadline = time.monotonic() + self._timeout
        attempt = 0
        last_exc: Exception | None = None
        while True:
            attempt += 1
            try:
                conn = libsql.connect(
                    database=self._url, auth_token=self._auth_token
                )
                conn.execute("SELECT 1")
                conn.commit()
                logger.info(
                    "system_db_connected", url=self._url, attempts=attempt
                )
                return conn
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if time.monotonic() >= deadline:
                    break
                logger.warning(
                    "system_db_connect_retry",
                    url=self._url,
                    attempt=attempt,
                    error=str(exc),
                )
                time.sleep(min(0.5 * attempt, 2.0))
        raise SystemDBError(
            f"Could not connect to system_db at {self._url} "
            f"after {attempt} attempt(s): {last_exc}"
        )

    # ------------------------------------------------------------------
    # DB-API pass-through (read path; safe without the write lock)
    # v1.9.0: auto-reconnect on a dead/stale connection. libsql http
    # connections can be closed server-side after long idle periods; without
    # this every control-plane query silently fails once the cached
    # connection dies (only RbacStore's serve_stale masks the failure, so
    # identity/token/task-history/user-state all break). Rebuild once, retry.
    def _reconnect(self) -> None:
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            pass
        self._conn = self._connect_with_retry()

    def execute(self, sql: str, params: tuple = ()) -> Any:
        try:
            return self._conn.execute(sql, params)
        except Exception:
            self._reconnect()
            return self._conn.execute(sql, params)

    def executemany(self, sql: str, params: list[tuple]) -> Any:
        try:
            return self._conn.executemany(sql, params)
        except Exception:
            self._reconnect()
            return self._conn.executemany(sql, params)

    def executescript(self, sql: str) -> Any:
        try:
            return self._conn.executescript(sql)
        except Exception:
            self._reconnect()
            return self._conn.executescript(sql)

    def commit(self) -> None:
        try:
            self._conn.commit()
        except Exception:
            self._reconnect()
            self._conn.commit()

    def with_write(self) -> "_WriteGuard":
        """Serialize a write transaction through a process-local RLock."""
        return _WriteGuard(self)

    # ------------------------------------------------------------------
    def health(self) -> bool:
        """Return True if the connection can serve ``SELECT 1``."""
        try:
            self._conn.execute("SELECT 1")
            self._conn.commit()
            return True
        except Exception:  # noqa: BLE001
            return False

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            pass


class _WriteGuard:
    """Context manager: acquire write lock, commit on exit.

    libSQL serializes writes; this guard makes a block of mutations
    thread-safe within one worker process::

        with db.with_write() as w:
            w.execute("INSERT ...", params)
            w.execute("UPDATE ...", params)
        # commit + release lock
    """

    __slots__ = ("_db",)

    def __init__(self, db: SystemDB) -> None:
        self._db = db

    def __enter__(self) -> SystemDB:
        self._db._write_lock.acquire()
        return self._db

    def __exit__(self, *exc: Any) -> None:
        try:
            self._db.commit()
        finally:
            self._db._write_lock.release()


def iter_pending() -> Iterator[None]:  # pragma: no cover - placeholder for tooling
    """Reserved for future streaming helpers."""
    return iter(())
