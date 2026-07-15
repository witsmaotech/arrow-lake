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

        # Drop pooled AsyncTable handle (#1): the cached view still references
        # the dropped index.
        from arrow_lake.query.async_conn_pool import invalidate_async_table

        invalidate_async_table(name, getattr(self, "_connect_uri", None))

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

        # Drop pooled AsyncTable handle (#1): its cached view predates the
        # index rebuild and may serve stale results.
        from arrow_lake.query.async_conn_pool import invalidate_async_table

        invalidate_async_table(name, getattr(self, "_connect_uri", None))

    # Default columns / type map for create_facet_indexes (v1.7.1 #3).
    # Low-cardinality columns → BITMAP; ordered/time/numeric → BTREE.
    # [#P3] chunk_index / document_id added: common WHERE filters on ingested
    # tables (chunk range scans, doc-level equality). Missing columns are skipped
    # by create_facet_indexes, so non-ingest tables are unaffected.
    _DEFAULT_FACET_COLUMNS: tuple[str, ...] = (
        "modality",
        "source",
        "doc_type",
        "created_at",
        "quality_score",
        "chunk_index",
        "document_id",
    )
    _DEFAULT_SCALAR_TYPE_MAP: dict[str, str] = {
        "modality": "BITMAP",
        "source": "BITMAP",
        "doc_type": "BITMAP",
        "created_at": "BTREE",
        "quality_score": "BTREE",
        "chunk_index": "BTREE",
        "document_id": "BTREE",
    }

    def create_scalar_index(
        self,
        name: str,
        column: str,
        *,
        index_type: str = "BTREE",
        replace: bool = True,
        index_name: str | None = None,
    ) -> None:
        """Create a scalar index on a column of a dataset (v1.7.1 #3).

        Args:
            name: Dataset name.
            column: Column to index.
            index_type: Scalar index type (BTREE/BITMAP/ZONEMAP/...).
            replace: Overwrite an existing index on this column.
            index_name: Optional explicit index name.

        Raises:
            StorageError: If dataset/column not found or index creation fails.
        """
        self._validate_name(name)
        self._validate_identifier(column, "column")
        table = self._open_lance(self._get_dataset_path(name))
        kwargs: dict[str, Any] = dict(index_type=index_type, replace=replace)
        if index_name is not None:
            kwargs["name"] = index_name
        try:
            table.create_scalar_index(column, **kwargs)
        except (ValueError, RuntimeError, OSError) as exc:
            raise StorageError(
                error_code=ErrorCode.SCALAR_INDEX_FAILED,
                message=(
                    f"Failed to create scalar index on '{column}' "
                    f"of dataset '{name}': {exc}"
                ),
            ) from exc

    def create_facet_indexes(
        self,
        name: str,
        columns: list[str] | None = None,
        *,
        type_map: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Create scalar indexes on facet columns in bulk (v1.7.1 #3).

        Missing columns are skipped (not errors). Per-column failures are
        recorded as "failed" without aborting the batch.

        Args:
            name: Dataset name.
            columns: Columns to index (None = default facet set).
            type_map: Per-column index type override (None = default heuristic).

        Returns:
            Mapping of column → status ("created"|"skipped"|"failed").
        """
        self._validate_name(name)
        cols = list(columns) if columns is not None else list(self._DEFAULT_FACET_COLUMNS)
        tmap = dict(self._DEFAULT_SCALAR_TYPE_MAP)
        if type_map is not None:
            tmap.update(type_map)

        table = self._open_lance(self._get_dataset_path(name))
        try:
            present = set(table.schema.names)
        except (ValueError, RuntimeError, AttributeError):
            present = set(cols)

        results: dict[str, str] = {}
        for col in cols:
            if col not in present:
                results[col] = "skipped"
                continue
            idx_type = tmap.get(col, "BTREE")
            try:
                table.create_scalar_index(col, index_type=idx_type, replace=True)
                results[col] = "created"
            except (ValueError, RuntimeError, OSError):
                results[col] = "failed"
        return results
