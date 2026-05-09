"""QueryEngine protocol — abstraction for DuckDB and future distributed OLAP."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class QueryEngine(Protocol):
    """Minimal interface for query session management.

    DuckDBSessionManager satisfies this protocol implicitly (duck typing).
    Future distributed engines (HTAP, StarRocks) can implement it for
    transparent swapping via the Lake facade.
    """

    def acquire(self, *, timeout: float | None = None, load_ducklake: bool = False) -> Any: ...

    def get_stats(self) -> Any: ...

    def shutdown(self) -> None: ...

    @property
    def pool_size(self) -> int: ...
