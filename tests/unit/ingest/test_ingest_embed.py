"""Unit tests for ingest_embed pipeline — Daft repositioning Sprint 4."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from arrow_lake.ingest.ingest_embed import IngestEmbedPipeline


@pytest.fixture
def mock_storage() -> MagicMock:
    return MagicMock()


@pytest.fixture
def pipeline(mock_storage: MagicMock) -> IngestEmbedPipeline:
    return IngestEmbedPipeline(
        storage=mock_storage,
        model="test-model",
        provider="transformers",
        num_partitions=2,
    )


class TestIngestAndEmbedValidation:
    def test_empty_paths_raises(self, pipeline: IngestEmbedPipeline) -> None:
        from arrow_lake.exceptions import IngestError
        with pytest.raises(IngestError, match="No file paths"):
            pipeline.ingest_and_embed("test", [])

    def test_unsupported_types_raises(self, pipeline: IngestEmbedPipeline) -> None:
        from arrow_lake.exceptions import IngestError
        with pytest.raises(IngestError, match="No supported file types"):
            pipeline.ingest_and_embed("test", ["/data/a.txt", "/data/b.xml"])


class TestIngestAndEmbedMocked:
    @patch("arrow_lake.ingest.ingest_embed.IngestEmbedPipeline._build_embed_expr")
    @patch("arrow_lake.ingest.ingest_embed.IngestEmbedPipeline._infer_dim_from_df")
    def test_basic_pipeline(
        self,
        mock_dim: MagicMock,
        mock_embed: MagicMock,
        pipeline: IngestEmbedPipeline,
        mock_storage: MagicMock,
    ) -> None:
        mock_embed.return_value = "mock_embed_expr"
        mock_dim.return_value = 128

        mock_df = MagicMock()
        mock_count_df = MagicMock()
        mock_count_arrow = MagicMock()
        mock_count_arrow.__getitem__ = MagicMock(
            return_value=MagicMock(as_py=MagicMock(return_value=100))
        )
        mock_count_df.to_arrow.return_value = mock_count_arrow
        mock_df.count.return_value = mock_count_df
        mock_df.into_partitions.return_value = mock_df
        mock_df.with_column.return_value = mock_df

        with patch("arrow_lake.ingest.ingest_embed.IngestEmbedPipeline._read_group", return_value=mock_df):
            result = pipeline.ingest_and_embed("test", ["/data/a.csv"])

        assert result.embedded_rows == 100
        assert result.embedding_dim == 128
        assert result.vector_column == "text_embedding"
        assert result.ingestion.total_files == 1
        mock_storage.write_lance_from_dataframe.assert_called_once()

    @patch("arrow_lake.ingest.ingest_embed.IngestEmbedPipeline._build_embed_expr")
    @patch("arrow_lake.ingest.ingest_embed.IngestEmbedPipeline._infer_dim_from_df")
    def test_with_transforms(
        self,
        mock_dim: MagicMock,
        mock_embed: MagicMock,
        pipeline: IngestEmbedPipeline,
        mock_storage: MagicMock,
    ) -> None:
        mock_embed.return_value = "mock_embed_expr"
        mock_dim.return_value = 64

        mock_df = MagicMock()
        mock_count_df = MagicMock()
        mock_count_arrow = MagicMock()
        mock_count_arrow.__getitem__ = MagicMock(
            return_value=MagicMock(as_py=MagicMock(return_value=50))
        )
        mock_count_df.to_arrow.return_value = mock_count_arrow
        mock_df.count.return_value = mock_count_df
        mock_df.into_partitions.return_value = mock_df
        mock_df.with_column.return_value = mock_df

        transform = MagicMock(return_value=mock_df)

        with patch("arrow_lake.ingest.ingest_embed.IngestEmbedPipeline._read_group", return_value=mock_df):
            result = pipeline.ingest_and_embed(
                "test", ["/a.csv"],
                transforms=[transform],
                embedding_column="my_emb",
            )

        transform.assert_called_once_with(mock_df)
        assert result.vector_column == "my_emb"


class TestReadGroup:
    def test_unsupported_type_raises(self, pipeline: IngestEmbedPipeline) -> None:
        from arrow_lake.exceptions import IngestError
        with pytest.raises(IngestError, match="unsupported"):
            pipeline._read_group(["/a.xml"], "xml")


class TestInferDimFromDf:
    def test_sample_success(self, pipeline: IngestEmbedPipeline) -> None:
        mock_df = MagicMock()
        mock_arrow = MagicMock()
        mock_val = MagicMock()
        mock_val.as_py.return_value = [0.1, 0.2, 0.3]
        mock_arrow.column.return_value = [mock_val]
        mock_df.select.return_value.limit.return_value.to_arrow.return_value = mock_arrow

        dim = pipeline._infer_dim_from_df(mock_df, "emb")
        assert dim == 3

    def test_sample_failure_returns_zero(self, pipeline: IngestEmbedPipeline) -> None:
        mock_df = MagicMock()
        mock_df.select.side_effect = RuntimeError("oops")
        assert pipeline._infer_dim_from_df(mock_df, "emb") == 0
