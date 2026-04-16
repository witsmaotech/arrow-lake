"""Shared Protocol definitions for query bridges.

Defines the structural interface that all search bridges follow,
enabling type-safe polymorphism without forcing a common base class.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import pyarrow as pa

__all__ = ["SearchBridge"]


@runtime_checkable
class SearchBridge(Protocol):
    """Protocol for search query bridges.

    All query bridges (OLAP, vector, FTS, hybrid, faceted) implement
    this interface. ExportBridge has a different shape and is excluded.
    """

    @property
    def name(self) -> str:
        """Bridge name for registry identification."""
        ...

    def search(self, dataset_name: str, **kwargs: Any) -> pa.Table:
        """Execute a search query against a dataset.

        Args:
            dataset_name: Name of the dataset to query.
            **kwargs: Bridge-specific search parameters.

        Returns:
            Arrow Table with search results.
        """
        ...
