"""Protocol interfaces for core Arrow Lake components.

Used for type-safe component references without concrete class dependencies.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class StorageProtocol(Protocol):
    """Minimal interface for LanceStorageManager."""

    def create_dataset(self, name: str, data: Any) -> None: ...
    def read_dataset(self, name: str, version: int | None = None, columns: list[str] | None = None) -> Any: ...
    def append_dataset(self, name: str, data: Any) -> None: ...
    def delete_dataset(self, name: str) -> None: ...
    def dataset_exists(self, name: str) -> bool: ...
    def list_datasets(self) -> list[str]: ...
    def open_dataset(self, name: str, version: int | None = None) -> Any: ...
    def dataset_uri(self, name: str) -> str: ...


@runtime_checkable
class EmbeddingEncoderProtocol(Protocol):
    """Minimal interface for embedding encoders."""

    def encode(self, texts: list[str]) -> Any: ...


@runtime_checkable
class KGClientProtocol(Protocol):
    """Minimal interface for knowledge graph client."""

    async def gremlin(self, query: str) -> list[dict[str, Any]]: ...
    async def get_stats(self) -> dict[str, Any]: ...
