"""StorageIndexingMixin -- vector index management operations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from arrow_lake.exceptions import ErrorCode, StorageError

if TYPE_CHECKING:
    from typing import Any


class StorageIndexingMixin:
    """Vector index operations for Lance datasets."""

    def delete_vector_index(
        self,
        name: str,
        index_name: str,
    ) -> None:
        """Delete a vector index from a dataset.

        Uses the lance SDK directly because LanceDB does not expose
        index deletion at the table level.

        Args:
            name: Dataset name.
            index_name: Name of the index to delete.

        Raises:
            StorageError: If dataset or index not found.
        """
        self._validate_name(name)
        import lance

        lance_uri = self.dataset_uri(name)
        try:
            ds = lance.dataset(lance_uri, storage_options=self._storage_options)
            ds.drop_index(index_name)
        except (ValueError, RuntimeError, OSError) as exc:
            raise StorageError(
                error_code=ErrorCode.QUERY_INDEX_NOT_FOUND,
                message=f"Failed to drop index '{index_name}' on dataset '{name}': {exc}",
            ) from exc

    def rebuild_vector_index(
        self,
        name: str,
        *,
        old_index_name: str | None = None,
        metric: str = "cosine",
        vector_column: str = "text_embedding",
        index_type: str = "IVF_PQ",
        num_partitions: int | None = None,
        num_sub_vectors: int | None = None,
    ) -> None:
        """Rebuild a vector index by dropping the old one and creating a new one.

        Args:
            name: Dataset name.
            old_index_name: Name of the existing index to drop (None = auto-detect).
            metric: Distance metric for the new index.
            vector_column: Vector column to index.
            index_type: LanceDB index type.
            num_partitions: IVF partitions (None = auto).
            num_sub_vectors: PQ sub-vectors (None = auto).

        Raises:
            StorageError: If dataset not found or index operations fail.
        """
        self._validate_name(name)
        self._validate_identifier(vector_column, "vector_column")

        if old_index_name is None:
            table = self._open_lance(self._get_dataset_path(name))
            try:
                indices = list(table.list_indices())
                for idx_config in indices:
                    cols = idx_config.columns if hasattr(idx_config, "columns") else []
                    if vector_column in cols:
                        old_index_name = idx_config.name if hasattr(idx_config, "name") else str(idx_config)
                        break
            except (ValueError, RuntimeError, OSError):
                pass

        if old_index_name is not None:
            self.delete_vector_index(name, old_index_name)

        table = self._open_lance(self._get_dataset_path(name))
        create_kwargs: dict[str, Any] = dict(
            metric=metric,
            vector_column_name=vector_column,
            index_type=index_type,
            replace=False,
        )
        if num_partitions is not None:
            create_kwargs["num_partitions"] = num_partitions
        if num_sub_vectors is not None:
            create_kwargs["num_sub_vectors"] = num_sub_vectors
        try:
            table.create_index(**create_kwargs)
        except (ValueError, RuntimeError, OSError) as exc:
            raise StorageError(
                error_code=ErrorCode.VECTOR_INDEX_FAILED,
                message=f"Failed to rebuild index on dataset '{name}': {exc}",
            ) from exc
