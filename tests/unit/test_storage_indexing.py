"""Tests for StorageIndexingMixin -- vector index management operations.

Covers:
- delete_vector_index: normal flow + error paths (ValueError, RuntimeError, OSError)
- rebuild_vector_index: auto-detect old index, explicit old_index_name,
  parameterised index creation, and failure handling
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from arrow_lake.exceptions import ErrorCode, StorageError
from arrow_lake.ingest._storage_indexing import StorageIndexingMixin

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mixin() -> StorageIndexingMixin:
    """Build a StorageIndexingMixin with all dependencies mocked."""
    obj = StorageIndexingMixin()
    obj._validate_name = MagicMock()  # type: ignore[assignment]
    obj._validate_identifier = MagicMock()  # type: ignore[assignment]
    obj._get_dataset_path = MagicMock(return_value="/tmp/data/test_ds")  # type: ignore[assignment]
    obj._open_lance = MagicMock()  # type: ignore[assignment]
    obj.dataset_uri = MagicMock(return_value="/tmp/data/test_ds.lance")  # type: ignore[assignment]
    obj._storage_options = None  # type: ignore[assignment]
    return obj


def _fake_index_config(
    name: str = "text_embedding_idx",
    columns: list[str] | None = None,
) -> SimpleNamespace:
    """Create a lightweight index config stand-in with name/columns attrs."""
    return SimpleNamespace(
        name=name,
        columns=columns or ["text_embedding"],
    )


# Mocking strategy note: ``delete_vector_index`` lazy-imports ``lance`` and
# ``arrow_lake.query.async_conn_pool`` (which pulls in the native ``lancedb``).
# We patch attributes on the REAL modules (``patch("lance.dataset", ...)``)
# instead of faking ``sys.modules["lance"]`` -- patch.dict(sys.modules, ...)
# evicts every module first-imported inside its window on exit, so the first
# test evicted ``lancedb`` and every later lazy import re-executed its pyo3
# module body (env_logger double-init panic). Attribute patching never touches
# sys.modules, making the suite order-coupling-free in both directions.


# ---------------------------------------------------------------------------
# delete_vector_index
# ---------------------------------------------------------------------------


class TestDeleteVectorIndex:
    """Tests for StorageIndexingMixin.delete_vector_index."""

    def test_calls_validate_name(self) -> None:
        """delete_vector_index validates the dataset name first."""
        mixin = _make_mixin()
        mock_ds = MagicMock()
        mock_ds.drop_index = MagicMock()

        with patch("lance.dataset", return_value=mock_ds):
            mixin.delete_vector_index("my_ds", "my_idx")

        mixin._validate_name.assert_called_once_with("my_ds")

    def test_opens_dataset_with_correct_uri(self) -> None:
        """delete_vector_index opens the lance dataset at the correct URI."""
        mixin = _make_mixin()
        mock_ds = MagicMock()
        mock_ds.drop_index = MagicMock()

        with patch("lance.dataset", return_value=mock_ds) as mock_dataset:
            mixin.delete_vector_index("my_ds", "my_idx")

        mock_dataset.assert_called_once_with(
            "/tmp/data/test_ds.lance",
            storage_options=None,
        )

    def test_calls_drop_index(self) -> None:
        """delete_vector_index delegates to ds.drop_index."""
        mixin = _make_mixin()
        mock_ds = MagicMock()

        with patch("lance.dataset", return_value=mock_ds):
            mixin.delete_vector_index("my_ds", "my_idx")

        mock_ds.drop_index.assert_called_once_with("my_idx")

    def test_value_error_raises_storage_error(self) -> None:
        """ValueError from lance wraps into StorageError(QUERY_INDEX_NOT_FOUND)."""
        mixin = _make_mixin()

        with patch("lance.dataset", side_effect=ValueError("not found")):
            with pytest.raises(StorageError) as exc_info:
                mixin.delete_vector_index("my_ds", "bad_idx")

            assert exc_info.value.error_code == ErrorCode.QUERY_INDEX_NOT_FOUND
            assert "bad_idx" in exc_info.value.message
            assert "my_ds" in exc_info.value.message

    def test_runtime_error_raises_storage_error(self) -> None:
        """RuntimeError from drop_index wraps into StorageError."""
        mixin = _make_mixin()
        mock_ds = MagicMock()
        mock_ds.drop_index.side_effect = RuntimeError("io error")

        with patch("lance.dataset", return_value=mock_ds):
            with pytest.raises(StorageError) as exc_info:
                mixin.delete_vector_index("my_ds", "my_idx")

            assert exc_info.value.error_code == ErrorCode.QUERY_INDEX_NOT_FOUND

    def test_os_error_raises_storage_error(self) -> None:
        """OSError from drop_index wraps into StorageError."""
        mixin = _make_mixin()
        mock_ds = MagicMock()
        mock_ds.drop_index.side_effect = OSError("permission denied")

        with patch("lance.dataset", return_value=mock_ds):
            with pytest.raises(StorageError) as exc_info:
                mixin.delete_vector_index("my_ds", "my_idx")

            assert exc_info.value.error_code == ErrorCode.QUERY_INDEX_NOT_FOUND

    def test_passes_storage_options(self) -> None:
        """Storage options are forwarded to lance.dataset."""
        mixin = _make_mixin()
        mixin._storage_options = {"region": "us-east-1"}  # type: ignore[assignment]
        mock_ds = MagicMock()

        with patch("lance.dataset", return_value=mock_ds) as mock_dataset:
            mixin.delete_vector_index("my_ds", "my_idx")

        mock_dataset.assert_called_once_with(
            "/tmp/data/test_ds.lance",
            storage_options={"region": "us-east-1"},
        )


# ---------------------------------------------------------------------------
# rebuild_vector_index
# ---------------------------------------------------------------------------


class TestRebuildVectorIndex:
    """Tests for StorageIndexingMixin.rebuild_vector_index."""

    def test_validates_name_and_column(self) -> None:
        """rebuild_vector_index validates both dataset name and vector_column."""
        mixin = _make_mixin()
        mixin._open_lance.return_value = MagicMock()

        mixin.rebuild_vector_index("my_ds")

        mixin._validate_name.assert_called_once_with("my_ds")
        mixin._validate_identifier.assert_called_once_with("text_embedding", "vector_column")

    def test_auto_detects_existing_index(self) -> None:
        """When old_index_name is None, the method auto-detects from list_indices."""
        mixin = _make_mixin()
        fake_table = MagicMock()
        idx_cfg = _fake_index_config(name="auto_idx", columns=["text_embedding"])
        fake_table.list_indices.return_value = [idx_cfg]
        mixin._open_lance.return_value = fake_table

        # delete_vector_index must also be spied on
        mixin.delete_vector_index = MagicMock()  # type: ignore[assignment]

        with patch.object(mixin, "_open_lance", wraps=mixin._open_lance):
            mixin.rebuild_vector_index("my_ds")

        mixin.delete_vector_index.assert_called_once_with("my_ds", "auto_idx")

    def test_auto_detect_skips_unrelated_columns(self) -> None:
        """Auto-detect ignores indices that don't target the vector_column."""
        mixin = _make_mixin()
        fake_table = MagicMock()
        idx_other = _fake_index_config(name="other_idx", columns=["some_col"])
        fake_table.list_indices.return_value = [idx_other]
        mixin._open_lance.return_value = fake_table
        mixin.delete_vector_index = MagicMock()  # type: ignore[assignment]

        mixin.rebuild_vector_index("my_ds", vector_column="text_embedding")

        mixin.delete_vector_index.assert_not_called()

    def test_explicit_old_index_name_deletes_before_create(self) -> None:
        """When old_index_name is given, delete is called before create."""
        mixin = _make_mixin()
        fake_table = MagicMock()
        mixin._open_lance.return_value = fake_table
        mixin.delete_vector_index = MagicMock()  # type: ignore[assignment]

        mixin.rebuild_vector_index("my_ds", old_index_name="old_idx")

        mixin.delete_vector_index.assert_called_once_with("my_ds", "old_idx")
        fake_table.create_index.assert_called_once()

    def test_create_index_default_kwargs(self) -> None:
        """create_index receives the correct default arguments."""
        mixin = _make_mixin()
        fake_table = MagicMock()
        mixin._open_lance.return_value = fake_table

        mixin.rebuild_vector_index("my_ds")

        fake_table.create_index.assert_called_once_with(
            metric="cosine",
            vector_column_name="text_embedding",
            index_type="IVF_PQ",
            replace=False,
        )

    def test_create_index_custom_metric_and_type(self) -> None:
        """Custom metric and index_type are forwarded."""
        mixin = _make_mixin()
        fake_table = MagicMock()
        mixin._open_lance.return_value = fake_table

        mixin.rebuild_vector_index(
            "my_ds",
            metric="l2",
            index_type="IVF_HNSW_SQ",
            vector_column="custom_vec",
        )

        fake_table.create_index.assert_called_once_with(
            metric="l2",
            vector_column_name="custom_vec",
            index_type="IVF_HNSW_SQ",
            replace=False,
        )

    def test_create_index_with_num_partitions(self) -> None:
        """num_partitions is included when provided."""
        mixin = _make_mixin()
        fake_table = MagicMock()
        mixin._open_lance.return_value = fake_table

        mixin.rebuild_vector_index("my_ds", num_partitions=64)

        call_kwargs = fake_table.create_index.call_args[1]
        assert call_kwargs["num_partitions"] == 64

    def test_create_index_with_num_sub_vectors(self) -> None:
        """num_sub_vectors is included when provided."""
        mixin = _make_mixin()
        fake_table = MagicMock()
        mixin._open_lance.return_value = fake_table

        mixin.rebuild_vector_index("my_ds", num_sub_vectors=16)

        call_kwargs = fake_table.create_index.call_args[1]
        assert call_kwargs["num_sub_vectors"] == 16

    def test_create_index_omits_none_params(self) -> None:
        """num_partitions/num_sub_vectors are absent when None (default)."""
        mixin = _make_mixin()
        fake_table = MagicMock()
        mixin._open_lance.return_value = fake_table

        mixin.rebuild_vector_index("my_ds")

        call_kwargs = fake_table.create_index.call_args[1]
        assert "num_partitions" not in call_kwargs
        assert "num_sub_vectors" not in call_kwargs

    def test_create_index_failure_raises_storage_error(self) -> None:
        """RuntimeError from create_index wraps into StorageError(VECTOR_INDEX_FAILED)."""
        mixin = _make_mixin()
        fake_table = MagicMock()
        fake_table.create_index.side_effect = RuntimeError("insufficient rows")
        mixin._open_lance.return_value = fake_table

        with pytest.raises(StorageError) as exc_info:
            mixin.rebuild_vector_index("my_ds")

        assert exc_info.value.error_code == ErrorCode.VECTOR_INDEX_FAILED
        assert "my_ds" in exc_info.value.message

    def test_value_error_on_create_raises_storage_error(self) -> None:
        """ValueError from create_index wraps into StorageError(VECTOR_INDEX_FAILED)."""
        mixin = _make_mixin()
        fake_table = MagicMock()
        fake_table.create_index.side_effect = ValueError("bad param")
        mixin._open_lance.return_value = fake_table

        with pytest.raises(StorageError) as exc_info:
            mixin.rebuild_vector_index("my_ds")

        assert exc_info.value.error_code == ErrorCode.VECTOR_INDEX_FAILED

    def test_os_error_on_create_raises_storage_error(self) -> None:
        """OSError from create_index wraps into StorageError(VECTOR_INDEX_FAILED)."""
        mixin = _make_mixin()
        fake_table = MagicMock()
        fake_table.create_index.side_effect = OSError("disk full")
        mixin._open_lance.return_value = fake_table

        with pytest.raises(StorageError) as exc_info:
            mixin.rebuild_vector_index("my_ds")

        assert exc_info.value.error_code == ErrorCode.VECTOR_INDEX_FAILED

    def test_auto_detect_tolerates_list_indices_error(self) -> None:
        """If list_indices raises, auto-detect silently falls through."""
        mixin = _make_mixin()
        fake_table = MagicMock()
        fake_table.list_indices.side_effect = RuntimeError("unsupported")
        mixin._open_lance.return_value = fake_table
        mixin.delete_vector_index = MagicMock()  # type: ignore[assignment]

        # Should not raise, should proceed to create_index
        mixin.rebuild_vector_index("my_ds")

        mixin.delete_vector_index.assert_not_called()
        fake_table.create_index.assert_called_once()

    def test_auto_detect_index_config_without_columns_attr(self) -> None:
        """Index config objects without 'columns' attr don't crash auto-detect."""
        mixin = _make_mixin()
        fake_table = MagicMock()
        # Index config without 'columns' attribute
        bare_idx = SimpleNamespace(name="bare_idx")
        fake_table.list_indices.return_value = [bare_idx]
        mixin._open_lance.return_value = fake_table
        mixin.delete_vector_index = MagicMock()  # type: ignore[assignment]

        mixin.rebuild_vector_index("my_ds")

        # Should not match, so no delete called
        mixin.delete_vector_index.assert_not_called()

    def test_auto_detect_index_config_without_name_attr(self) -> None:
        """Index config without 'name' attr falls back to str()."""
        mixin = _make_mixin()
        fake_table = MagicMock()
        # Index config with columns but no name
        no_name_idx = SimpleNamespace(columns=["text_embedding"])
        fake_table.list_indices.return_value = [no_name_idx]
        mixin._open_lance.return_value = fake_table
        mixin.delete_vector_index = MagicMock()  # type: ignore[assignment]

        mixin.rebuild_vector_index("my_ds")

        expected_name = str(no_name_idx)
        mixin.delete_vector_index.assert_called_once_with("my_ds", expected_name)

    def test_auto_detect_uses_custom_vector_column(self) -> None:
        """Auto-detect matches against the custom vector_column parameter."""
        mixin = _make_mixin()
        fake_table = MagicMock()
        idx_cfg = _fake_index_config(name="vec_idx", columns=["custom_vec"])
        fake_table.list_indices.return_value = [idx_cfg]
        mixin._open_lance.return_value = fake_table
        mixin.delete_vector_index = MagicMock()  # type: ignore[assignment]

        mixin.rebuild_vector_index("my_ds", vector_column="custom_vec")

        mixin.delete_vector_index.assert_called_once_with("my_ds", "vec_idx")

    def test_all_params_combined(self) -> None:
        """Full parameter set exercises all branches in a single call."""
        mixin = _make_mixin()
        fake_table = MagicMock()
        mixin._open_lance.return_value = fake_table
        mixin.delete_vector_index = MagicMock()  # type: ignore[assignment]

        mixin.rebuild_vector_index(
            "my_ds",
            old_index_name="old_ivf",
            metric="dot",
            vector_column="dense_vec",
            index_type="IVF_HNSW_SQ",
            num_partitions=128,
            num_sub_vectors=32,
        )

        mixin.delete_vector_index.assert_called_once_with("my_ds", "old_ivf")
        fake_table.create_index.assert_called_once_with(
            metric="dot",
            vector_column_name="dense_vec",
            index_type="IVF_HNSW_SQ",
            replace=False,
            num_partitions=128,
            num_sub_vectors=32,
        )


# ---------------------------------------------------------------------------
# create_scalar_index (v1.7.1 #3)
# ---------------------------------------------------------------------------


class TestCreateScalarIndex:
    """Tests for StorageIndexingMixin.create_scalar_index."""

    def test_validates_name_and_column(self) -> None:
        mixin = _make_mixin()
        mixin._open_lance.return_value = MagicMock()

        mixin.create_scalar_index("my_ds", "modality")

        mixin._validate_name.assert_called_once_with("my_ds")
        mixin._validate_identifier.assert_called_once_with("modality", "column")

    def test_default_btree_index_type(self) -> None:
        mixin = _make_mixin()
        fake_table = MagicMock()
        mixin._open_lance.return_value = fake_table

        mixin.create_scalar_index("my_ds", "modality")

        fake_table.create_scalar_index.assert_called_once_with(
            "modality", index_type="BTREE", replace=True
        )

    def test_custom_index_type_and_replace(self) -> None:
        mixin = _make_mixin()
        fake_table = MagicMock()
        mixin._open_lance.return_value = fake_table

        mixin.create_scalar_index("my_ds", "source", index_type="BITMAP", replace=False)

        fake_table.create_scalar_index.assert_called_once_with(
            "source", index_type="BITMAP", replace=False
        )

    def test_index_name_passed_when_given(self) -> None:
        mixin = _make_mixin()
        fake_table = MagicMock()
        mixin._open_lance.return_value = fake_table

        mixin.create_scalar_index("my_ds", "created_at", index_name="ca_idx")

        assert fake_table.create_scalar_index.call_args.kwargs["name"] == "ca_idx"

    def test_index_name_omitted_when_none(self) -> None:
        mixin = _make_mixin()
        fake_table = MagicMock()
        mixin._open_lance.return_value = fake_table

        mixin.create_scalar_index("my_ds", "created_at")

        assert "name" not in fake_table.create_scalar_index.call_args.kwargs

    def test_failure_raises_storage_error(self) -> None:
        mixin = _make_mixin()
        fake_table = MagicMock()
        fake_table.create_scalar_index.side_effect = RuntimeError("boom")
        mixin._open_lance.return_value = fake_table

        with pytest.raises(StorageError) as exc_info:
            mixin.create_scalar_index("my_ds", "modality")

        assert exc_info.value.error_code == ErrorCode.SCALAR_INDEX_FAILED
        assert "my_ds" in exc_info.value.message


# ---------------------------------------------------------------------------
# create_facet_indexes (v1.7.1 #3)
# ---------------------------------------------------------------------------


class TestCreateFacetIndexes:
    """Tests for StorageIndexingMixin.create_facet_indexes."""

    def test_creates_all_present_columns(self) -> None:
        mixin = _make_mixin()
        fake_table = MagicMock()
        fake_table.schema.names = ["modality", "source", "created_at"]
        mixin._open_lance.return_value = fake_table

        result = mixin.create_facet_indexes("my_ds", columns=["modality", "source", "created_at"])

        assert result == {"modality": "created", "source": "created", "created_at": "created"}
        assert fake_table.create_scalar_index.call_count == 3

    def test_skips_missing_columns(self) -> None:
        mixin = _make_mixin()
        fake_table = MagicMock()
        fake_table.schema.names = ["modality"]
        mixin._open_lance.return_value = fake_table

        result = mixin.create_facet_indexes("my_ds", columns=["modality", "ghost"])

        assert result == {"modality": "created", "ghost": "skipped"}
        fake_table.create_scalar_index.assert_called_once_with(
            "modality", index_type="BITMAP", replace=True
        )

    def test_default_type_map(self) -> None:
        mixin = _make_mixin()
        fake_table = MagicMock()
        fake_table.schema.names = ["modality", "doc_type", "created_at", "quality_score"]
        mixin._open_lance.return_value = fake_table

        mixin.create_facet_indexes(
            "my_ds", columns=["modality", "doc_type", "created_at", "quality_score"]
        )

        calls = {
            c.args[0]: c.kwargs["index_type"]
            for c in fake_table.create_scalar_index.call_args_list
        }
        assert calls["modality"] == "BITMAP"
        assert calls["doc_type"] == "BITMAP"
        assert calls["created_at"] == "BTREE"
        assert calls["quality_score"] == "BTREE"

    def test_custom_type_map_overrides_default(self) -> None:
        mixin = _make_mixin()
        fake_table = MagicMock()
        fake_table.schema.names = ["modality"]
        mixin._open_lance.return_value = fake_table

        mixin.create_facet_indexes(
            "my_ds", columns=["modality"], type_map={"modality": "ZONEMAP"}
        )

        fake_table.create_scalar_index.assert_called_once_with(
            "modality", index_type="ZONEMAP", replace=True
        )

    def test_failure_recorded_not_raised(self) -> None:
        mixin = _make_mixin()
        fake_table = MagicMock()
        fake_table.schema.names = ["modality", "source"]

        def _fake_create(col: str, **_: object) -> None:
            if col == "source":
                raise RuntimeError("x")

        fake_table.create_scalar_index.side_effect = _fake_create
        mixin._open_lance.return_value = fake_table

        result = mixin.create_facet_indexes("my_ds", columns=["modality", "source"])

        assert result["modality"] == "created"
        assert result["source"] == "failed"

    def test_default_columns_when_none(self) -> None:
        mixin = _make_mixin()
        fake_table = MagicMock()
        fake_table.schema.names = ["modality", "source", "doc_type", "created_at", "quality_score"]
        mixin._open_lance.return_value = fake_table

        result = mixin.create_facet_indexes("my_ds")

        assert set(result.keys()) == {"modality", "source", "doc_type", "created_at", "quality_score", "chunk_index", "document_id"}  # chunk_index/document_id joined the facet defaults
