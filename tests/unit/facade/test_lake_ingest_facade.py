"""Tests for _LakeIngestMixin facade methods — ingest, create/append/upsert, quality, dedup, embed_and_add."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest
from arrow_lake._lake_ingest import _LakeIngestMixin
from arrow_lake.config import ArrowLakeConfig
from arrow_lake.config._enums import EmbeddingBackend
from arrow_lake.config.media import QualityConfig


def _make_config() -> ArrowLakeConfig:
    cfg = ArrowLakeConfig()
    cfg.quality = QualityConfig(enabled=True)
    cfg.embedding.backend = EmbeddingBackend.LOCAL
    cfg.embedding.api_base = ""
    return cfg


class _TestLake(_LakeIngestMixin):
    """Thin wrapper to expose _LakeIngestMixin without full Lake.__init__."""

    def __init__(self, config: ArrowLakeConfig | None = None) -> None:
        self._config = config or _make_config()
        self._components: dict[str, object] = {}
        self._storage: MagicMock = MagicMock()

    def _get_component(self, key: str, factory) -> object:
        if key not in self._components:
            self._components[key] = factory()
        return self._components[key]

    def _get_storage(self) -> MagicMock:
        return self._storage


@pytest.fixture()
def lake() -> _TestLake:
    return _TestLake()


# ---------------------------------------------------------------------------
# Ingest delegation
# ---------------------------------------------------------------------------


class TestIngestDelegation:
    """Test that ingest* methods delegate to Ingestor."""

    @patch("arrow_lake.ingest.ingestor.Ingestor")
    def test_ingest_delegates(self, mock_cls: MagicMock, lake: _TestLake) -> None:
        mock_report = MagicMock()
        mock_cls.return_value.ingest.return_value = mock_report

        result = lake.ingest("ds", ["/tmp/f.csv"])
        assert result is mock_report
        mock_cls.return_value.ingest.assert_called_once_with("ds", ["/tmp/f.csv"], transforms=None)

    @patch("arrow_lake.ingest.ingestor.Ingestor")
    def test_ingest_http_delegates(self, mock_cls: MagicMock, lake: _TestLake) -> None:
        mock_report = MagicMock()
        mock_cls.return_value.ingest_http.return_value = mock_report

        result = lake.ingest_http("ds", ["http://example.com/d.json"])
        assert result is mock_report

    @patch("arrow_lake.ingest.ingestor.Ingestor")
    def test_ingest_images_delegates(self, mock_cls: MagicMock, lake: _TestLake) -> None:
        mock_report = MagicMock()
        mock_cls.return_value.ingest_images.return_value = mock_report

        result = lake.ingest_images("ds", ["/img.png"])
        assert result is mock_report

    @patch("arrow_lake.ingest.ingestor.Ingestor")
    def test_ingest_videos_delegates(self, mock_cls: MagicMock, lake: _TestLake) -> None:
        mock_report = MagicMock()
        mock_cls.return_value.ingest_videos.return_value = mock_report

        result = lake.ingest_videos("ds", ["/vid.mp4"])
        assert result is mock_report

    @patch("arrow_lake.ingest.ingestor.Ingestor")
    def test_ingest_mixed_delegates(self, mock_cls: MagicMock, lake: _TestLake) -> None:
        mock_report = MagicMock()
        mock_cls.return_value.ingest_mixed.return_value = mock_report
        sources = {"files": ["/tmp/data.csv"]}

        result = lake.ingest_mixed("ds", sources)
        assert result is mock_report
        mock_cls.return_value.ingest_mixed.assert_called_once_with("ds", sources)

    @patch("arrow_lake.ingest.ingestor.Ingestor")
    @patch("arrow_lake.ingest.ocr.TurboOcrClient")
    @patch("arrow_lake.storage.blob_store.BlobStoreManager")
    def test_ingest_documents_delegates(self, mock_blob_cls: MagicMock, mock_ocr_cls: MagicMock, mock_ingestor_cls: MagicMock, lake: _TestLake) -> None:
        mock_report = MagicMock()
        mock_ingestor_cls.return_value.ingest_documents.return_value = mock_report

        result = lake.ingest_documents("ds", ["/doc.pdf"])
        assert result is mock_report
        mock_ingestor_cls.return_value.ingest_documents.assert_called_once()

    @patch("arrow_lake.ingest.ingestor.Ingestor")
    @patch("arrow_lake.ingest.ocr.TurboOcrClient")
    @patch("arrow_lake.storage.blob_store.BlobStoreManager")
    def test_ingest_documents_with_custom_config(self, mock_blob_cls: MagicMock, mock_ocr_cls: MagicMock, mock_ingestor_cls: MagicMock, lake: _TestLake) -> None:
        mock_report = MagicMock()
        mock_ingestor_cls.return_value.ingest_documents.return_value = mock_report
        mock_config = MagicMock()
        mock_config.ocr_endpoint = "http://custom:9000"

        result = lake.ingest_documents("ds", ["/doc.pdf"], doc_config=mock_config)
        assert result is mock_report
        ocr_call_kwargs = mock_ocr_cls.call_args
        assert ocr_call_kwargs.kwargs.get("endpoint") == "http://custom:9000"


# ---------------------------------------------------------------------------
# create_dataset / append_dataset / upsert
# ---------------------------------------------------------------------------


class TestCreateDataset:
    """Test create_dataset with metrics and error paths."""

    def test_create_dataset_success(self, lake: _TestLake) -> None:
        table = pa.table({"id": [1, 2], "val": ["a", "b"]})
        with patch("arrow_lake.core.metrics.get_metrics_enabled", return_value=False), \
             patch("arrow_lake.api.telemetry.get_tracer") as mock_tracer:
            mock_tracer.return_value.start_as_current_span.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_tracer.return_value.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)
            lake.create_dataset("test_ds", table)
            lake._storage.create_dataset.assert_called_once_with("test_ds", table)

    def test_create_dataset_rejects_non_table(self, lake: _TestLake) -> None:
        from arrow_lake.exceptions import ValidationError

        with pytest.raises(ValidationError, match="pyarrow.Table"):
            lake.create_dataset("bad", "not_a_table")  # type: ignore

    def test_create_dataset_records_metrics_on_success(self, lake: _TestLake) -> None:
        with patch("arrow_lake.core.metrics.get_metrics_enabled", return_value=True), \
             patch("arrow_lake.core.metrics.ingestion_rows_total") as mock_rows, \
             patch("arrow_lake.core.metrics.ingestion_bytes_total") as mock_bytes, \
             patch("arrow_lake.core.metrics.ingestion_duration_seconds") as mock_dur, \
             patch("arrow_lake.core.metrics.catalog_tables_total") as mock_tables, \
             patch("arrow_lake.api.telemetry.get_tracer") as mock_tracer:

            mock_tracer.return_value.start_as_current_span.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_tracer.return_value.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

            table = pa.table({"id": [1]})
            lake.create_dataset("metrics_ds", table)

            mock_rows.labels.assert_called()
            mock_bytes.labels.assert_called()
            mock_dur.labels.assert_called()
            mock_tables.inc.assert_called_once()

    def test_create_dataset_records_error_metrics(self, lake: _TestLake) -> None:
        from arrow_lake.exceptions import ErrorCode, StorageError

        lake._storage.create_dataset.side_effect = StorageError(ErrorCode.STORAGE_WRITE_FAILED, "exists")

        with patch("arrow_lake.core.metrics.get_metrics_enabled", return_value=True), \
             patch("arrow_lake.core.metrics.ingestion_errors_total") as mock_errs, \
             patch("arrow_lake.api.telemetry.get_tracer") as mock_tracer:

            mock_tracer.return_value.start_as_current_span.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_tracer.return_value.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

            table = pa.table({"id": [1]})
            with pytest.raises(StorageError):
                lake.create_dataset("dup_ds", table)

            mock_errs.labels.assert_called()


class TestAppendDataset:
    """Test append_dataset with metrics."""

    def test_append_success(self, lake: _TestLake) -> None:
        table = pa.table({"id": [1]})
        with patch("arrow_lake.core.metrics.get_metrics_enabled", return_value=False):
            lake.append_dataset("ds", table)
            lake._storage.append_dataset.assert_called_once_with("ds", table)

    def test_append_rejects_non_table(self, lake: _TestLake) -> None:
        from arrow_lake.exceptions import ValidationError

        with pytest.raises(ValidationError, match="pyarrow.Table"):
            lake.append_dataset("ds", 42)  # type: ignore

    def test_append_records_metrics(self, lake: _TestLake) -> None:
        with patch("arrow_lake.core.metrics.get_metrics_enabled", return_value=True), \
             patch("arrow_lake.core.metrics.ingestion_rows_total") as mock_rows, \
             patch("arrow_lake.core.metrics.ingestion_bytes_total") as mock_bytes, \
             patch("arrow_lake.core.metrics.ingestion_duration_seconds") as mock_dur:

            table = pa.table({"id": [1]})
            lake.append_dataset("ds", table)

            mock_rows.labels.assert_called()
            mock_bytes.labels.assert_called()
            mock_dur.labels.assert_called()

    def test_append_records_error_metrics(self, lake: _TestLake) -> None:
        from arrow_lake.exceptions import ErrorCode, StorageError

        lake._storage.append_dataset.side_effect = StorageError(ErrorCode.STORAGE_WRITE_FAILED, "missing")

        with patch("arrow_lake.core.metrics.get_metrics_enabled", return_value=True), \
             patch("arrow_lake.core.metrics.ingestion_errors_total") as mock_errs:
            table = pa.table({"id": [1]})
            with pytest.raises(StorageError):
                lake.append_dataset("missing", table)

            mock_errs.labels.assert_called()


class TestUpsert:
    """Test upsert method."""

    def test_upsert_success(self, lake: _TestLake) -> None:
        table = pa.table({"id": [1], "name": ["a"]})
        with patch("arrow_lake.core.metrics.get_metrics_enabled", return_value=False):
            lake.upsert("ds", table, on="id")
            lake._storage.upsert_dataset.assert_called_once_with("ds", table, on="id")

    def test_upsert_rejects_non_table(self, lake: _TestLake) -> None:
        from arrow_lake.exceptions import ValidationError

        with pytest.raises(ValidationError, match="pyarrow.Table"):
            lake.upsert("ds", {"not": "table"})  # type: ignore

    def test_upsert_records_error_metrics(self, lake: _TestLake) -> None:
        from arrow_lake.exceptions import ErrorCode, StorageError

        lake._storage.upsert_dataset.side_effect = StorageError(ErrorCode.STORAGE_WRITE_FAILED, "err")

        with patch("arrow_lake.core.metrics.get_metrics_enabled", return_value=True), \
             patch("arrow_lake.core.metrics.ingestion_errors_total") as mock_errs:
            table = pa.table({"id": [1]})
            with pytest.raises(StorageError):
                lake.upsert("ds", table)

            mock_errs.labels.assert_called()


# ---------------------------------------------------------------------------
# delete_rows / update_rows
# ---------------------------------------------------------------------------


class TestDeleteRows:
    def test_delete_rows_delegates(self, lake: _TestLake) -> None:
        lake._storage.delete_rows.return_value = 5

        result = lake.delete_rows("ds", "id > 3")
        assert result == 5
        lake._storage.delete_rows.assert_called_once_with("ds", "id > 3")


class TestUpdateRows:
    def test_update_rows_delegates(self, lake: _TestLake) -> None:
        lake.update_rows("ds", "id = 1", {"name": "'updated'"})
        lake._storage.update_rows.assert_called_once_with("ds", "id = 1", {"name": "'updated'"})


# ---------------------------------------------------------------------------
# quality_filter / deduplicate
# ---------------------------------------------------------------------------


class TestQualityFilter:
    """Test quality_filter delegates to QualityFilterRegistry."""

    def test_quality_filter_applies_all(self, lake: _TestLake) -> None:
        mock_table = pa.table({"text_content": ["hello", "hi"]})
        lake._storage.read_dataset.return_value = mock_table

        mock_report = MagicMock()
        mock_report.filter_results = []

        with patch("arrow_lake.core.metrics.get_metrics_enabled", return_value=False), \
             patch("arrow_lake.quality.base.QualityFilterRegistry") as mock_registry_cls:
            mock_registry = MagicMock()
            mock_registry_cls.return_value = mock_registry
            mock_registry.apply_all.return_value = mock_report

            result = lake.quality_filter("ds")
            assert result is mock_report

    def test_quality_filter_with_custom_filters(self, lake: _TestLake) -> None:
        lake._storage.read_dataset.return_value = pa.table({"text_content": ["x"]})

        with patch("arrow_lake.core.metrics.get_metrics_enabled", return_value=False), \
             patch("arrow_lake.quality.base.QualityFilterRegistry") as mock_registry_cls:
            mock_registry = MagicMock()
            mock_registry_cls.return_value = mock_registry
            mock_registry.apply_all.return_value = MagicMock(filter_results=[])

            lake.quality_filter("ds", active_filters="text_length", mode="any")

            mock_registry.apply_all.assert_called_once()

    def test_quality_filter_records_reject_metrics(self, lake: _TestLake) -> None:
        lake._storage.read_dataset.return_value = pa.table({"text_content": ["x"]})

        mock_fr = MagicMock(filter_name="text_length", rejected_count=3)
        mock_report = MagicMock(filter_results=[mock_fr])

        with patch("arrow_lake.core.metrics.get_metrics_enabled", return_value=True), \
             patch("arrow_lake.quality.base.QualityFilterRegistry") as mock_registry_cls, \
             patch("arrow_lake.core.metrics.processing_quality_rejects_total") as mock_rejects:

            mock_registry = MagicMock()
            mock_registry_cls.return_value = mock_registry
            mock_registry.apply_all.return_value = mock_report

            lake.quality_filter("ds")

            mock_rejects.labels.assert_called_with(filter_name="text_length")


class TestDeduplicate:
    """Test deduplicate delegates to ContentDeduplicator."""

    def test_deduplicate_with_defaults(self, lake: _TestLake) -> None:
        mock_table = pa.table({"text": ["a", "a", "b"]})
        lake._storage.read_dataset.return_value = mock_table

        with patch("arrow_lake.quality.dedup.ContentDeduplicator") as mock_cls:
            mock_dedup = MagicMock()
            mock_cls.return_value = mock_dedup
            mock_dedup.deduplicate.return_value = MagicMock()

            lake.deduplicate("ds")

            mock_cls.assert_called_once_with(
                strategy=lake._config.quality.dedup_strategy,
                action=lake._config.quality.dedup_action,
                perceptual_threshold=lake._config.quality.dedup_perceptual_threshold,
            )

    def test_deduplicate_with_custom_params(self, lake: _TestLake) -> None:
        lake._storage.read_dataset.return_value = pa.table({"text": ["a"]})

        with patch("arrow_lake.quality.dedup.ContentDeduplicator") as mock_cls:
            mock_dedup = MagicMock()
            mock_cls.return_value = mock_dedup
            mock_dedup.deduplicate.return_value = MagicMock()

            lake.deduplicate("ds", strategy="perceptual", action="remove", perceptual_threshold=5)

            mock_cls.assert_called_once_with(
                strategy="perceptual",
                action="remove",
                perceptual_threshold=5,
            )


class TestEmbedAndAdd:
    """Test embed_and_add uses configured backend."""

    def test_embed_local_backend(self, lake: _TestLake) -> None:
        import numpy as np

        mock_table = pa.table({"text_content": ["hello", "world"]})
        lake._storage.read_dataset.return_value = mock_table

        mock_result = MagicMock()
        mock_result.embeddings = np.array([[0.1, 0.2], [0.3, 0.4]])
        mock_result.embedding_dim = 2

        with patch("arrow_lake.embed.encoder.LocalEmbeddingEncoder") as mock_encoder_cls:
            mock_encoder = MagicMock()
            mock_encoder_cls.return_value = mock_encoder
            mock_encoder.encode_column.return_value = mock_result

            result = lake.embed_and_add("ds")

            assert result == 2
            lake._storage.add_columns_table.assert_called_once()

    def test_embed_api_backend(self, lake: _TestLake) -> None:
        import numpy as np
        from arrow_lake.config._enums import EmbeddingBackend

        lake._config.embedding.backend = EmbeddingBackend.OPENAI
        lake._config.embedding.api_base = "http://localhost:11434"

        mock_table = pa.table({"text_content": ["hello", "world"]})
        lake._storage.read_dataset.return_value = mock_table

        mock_result = MagicMock()
        mock_result.embeddings = np.array([[0.1, 0.2], [0.3, 0.4]])

        with patch("arrow_lake.embed.encoder.ApiEmbeddingEncoder") as mock_encoder_cls:
            mock_encoder = MagicMock()
            mock_encoder_cls.return_value = mock_encoder
            mock_encoder.encode.return_value = mock_result

            result = lake.embed_and_add("ds")

            assert result == 2
            mock_encoder_cls.assert_called_once()

    def test_embed_custom_batch_size(self, lake: _TestLake) -> None:
        import numpy as np

        mock_table = pa.table({"text_content": ["a"]})
        lake._storage.read_dataset.return_value = mock_table

        mock_result = MagicMock()
        mock_result.embeddings = np.array([[0.1, 0.2]])
        mock_result.embedding_dim = 2

        with patch("arrow_lake.embed.encoder.LocalEmbeddingEncoder") as mock_encoder_cls:
            mock_encoder = MagicMock()
            mock_encoder_cls.return_value = mock_encoder
            mock_encoder.encode_column.return_value = mock_result

            lake.embed_and_add("ds", batch_size=32)

            mock_encoder_cls.assert_called_once()
            call_kwargs = mock_encoder_cls.call_args.kwargs
            assert call_kwargs.get("batch_size") == 32

    def test_embed_custom_columns(self, lake: _TestLake) -> None:
        import numpy as np

        mock_table = pa.table({"body": ["text here"]})
        lake._storage.read_dataset.return_value = mock_table

        mock_result = MagicMock()
        mock_result.embeddings = np.array([[0.1, 0.2]])
        mock_result.embedding_dim = 2

        with patch("arrow_lake.embed.encoder.LocalEmbeddingEncoder") as mock_encoder_cls:
            mock_encoder = MagicMock()
            mock_encoder_cls.return_value = mock_encoder
            mock_encoder.encode_column.return_value = mock_result

            lake.embed_and_add("ds", text_column="body", embedding_column="body_vec")

            lake._storage.read_dataset.assert_called_once_with("ds", columns=["body"])

            call_args = mock_encoder.encode_column.call_args
            assert call_args.kwargs.get("column") == "body"

            add_call_args = lake._storage.add_columns_table.call_args
            vec_table = add_call_args[0][1]
            assert "body_vec" in vec_table.column_names
