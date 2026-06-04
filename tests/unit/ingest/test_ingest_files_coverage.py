"""Coverage for _FileIngestMixin — ingest, ingest_batch, ingest_documents, ingest_join, ingest_union."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

import pyarrow as pa
import pytest

from arrow_lake.exceptions import DocumentError, IngestError
from arrow_lake.ingest._ingest_files import _FileIngestMixin
from arrow_lake.ingest.ingestor import IngestionReport, IngestionSource


# ---------------------------------------------------------------------------
# Minimal host that satisfies _FileIngestMixin requirements
# ---------------------------------------------------------------------------


class _FakeHost:
    """Host providing _manager, _first_table_seen, _write_table, _build_report."""

    def __init__(self) -> None:
        self._manager = MagicMock()
        self._first_table_seen: dict[str, bool] = {}

    def _detect_file_type(self, path: str) -> str:
        ext = Path(path).suffix.lower()
        mapping = {".csv": "csv", ".json": "json", ".jsonl": "json", ".parquet": "parquet"}
        ft = mapping.get(ext)
        if ft is None:
            raise IngestError(
                error_code="INGEST_UNSUPPORTED_FORMAT",
                message=f"Unsupported file format for '{path}'",
            )
        return ft

    def _read_file_df(self, path: str, file_type: str) -> MagicMock:
        df = MagicMock()
        df.to_arrow.return_value = pa.table({"col": [1, 2]})
        return df

    def _write_table(
        self,
        dataset_name: str,
        table: pa.Table,
        sources: list[IngestionSource],
        source_path: str,
    ) -> None:
        sources.append(IngestionSource(path=source_path, row_count=table.num_rows))

    @staticmethod
    def _build_report(sources: list[IngestionSource]) -> IngestionReport:
        return IngestionReport(
            sources=tuple(sources),
            total_rows=sum(s.row_count for s in sources),
            total_files=sum(s.file_count for s in sources),
        )


class _HostWithMixin(_FakeHost, _FileIngestMixin):
    pass


@pytest.fixture
def host() -> _HostWithMixin:
    return _HostWithMixin()


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------


class TestIngest:
    def test_empty_paths_raises(self, host: _HostWithMixin) -> None:
        with pytest.raises(IngestError, match="No file paths provided"):
            host.ingest("ds", [])

    def test_single_csv(self, host: _HostWithMixin) -> None:
        report = host.ingest("ds", ["/data/a.csv"])
        assert report.total_rows == 2
        assert len(report.sources) == 1

    def test_multiple_files(self, host: _HostWithMixin) -> None:
        report = host.ingest("ds", ["/data/a.csv", "/data/b.json"])
        assert len(report.sources) == 2

    def test_with_transforms(self, host: _HostWithMixin) -> None:
        transformed_df = MagicMock()
        transformed_df.to_arrow.return_value = pa.table({"x": [10]})
        t = MagicMock(return_value=transformed_df)

        report = host.ingest("ds", ["/data/a.csv"], transforms=[t])
        t.assert_called_once()
        assert report.total_rows == 1

    def test_transforms_applied_sequentially(self, host: _HostWithMixin) -> None:
        final_df = MagicMock()
        final_df.to_arrow.return_value = pa.table({"x": [99]})

        t1 = MagicMock(return_value=MagicMock(to_arrow=MagicMock(return_value=pa.table({"x": [99]}))))
        # Make t1 return something with .to_arrow so next transform gets it
        intermediate = MagicMock()
        t1.return_value = intermediate
        t2 = MagicMock(return_value=final_df)

        # Override _read_file_df to return a df that .to_arrow() returns table
        orig_read = host._read_file_df
        read_df = MagicMock()
        read_df.to_arrow.return_value = pa.table({"col": [1]})
        host._read_file_df = lambda p, ft: read_df

        host.ingest("ds", ["/data/a.csv"], transforms=[t1, t2])
        t1.assert_called_once_with(read_df)
        t2.assert_called_once_with(intermediate)
        host._read_file_df = orig_read


# ---------------------------------------------------------------------------
# ingest_batch
# ---------------------------------------------------------------------------


class TestIngestBatch:
    def test_empty_paths_raises(self, host: _HostWithMixin) -> None:
        with pytest.raises(IngestError, match="No file paths provided for batch"):
            host.ingest_batch("ds", [])

    @patch("arrow_lake.ingest._ingest_files._FileIngestMixin._read_files_df")
    def test_single_type(self, mock_read: MagicMock, host: _HostWithMixin) -> None:
        mock_df = MagicMock()
        mock_count_arrow = MagicMock()
        mock_count_arrow.__getitem__ = MagicMock(return_value=MagicMock(as_py=MagicMock(return_value=7)))
        mock_df.count.return_value.to_arrow.return_value = mock_count_arrow
        mock_read.return_value = mock_df

        report = host.ingest_batch("ds", ["/a.csv", "/b.csv"])
        assert report.total_rows == 7
        assert report.sources[0].file_count == 2

    @patch("arrow_lake.ingest._ingest_files._FileIngestMixin._read_files_df")
    def test_with_transforms(self, mock_read: MagicMock, host: _HostWithMixin) -> None:
        mock_df = MagicMock()
        mock_count_arrow = MagicMock()
        mock_count_arrow.__getitem__ = MagicMock(return_value=MagicMock(as_py=MagicMock(return_value=3)))
        mock_df.count.return_value.to_arrow.return_value = mock_count_arrow
        mock_read.return_value = mock_df

        t = MagicMock(return_value=mock_df)
        host.ingest_batch("ds", ["/a.csv"], transforms=[t])
        t.assert_called_once_with(mock_df)


# ---------------------------------------------------------------------------
# ingest_documents
# ---------------------------------------------------------------------------


class TestIngestDocuments:
    @patch("arrow_lake.ingest.chunker.DocumentChunker")
    @patch("arrow_lake.ingest.document.DocumentParser")
    def test_default_config(self, MockParser: MagicMock, MockChunker: MagicMock, host: _HostWithMixin, tmp_path: Path) -> None:
        pdf = tmp_path / "sample.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake content")

        mock_parsed = MagicMock()
        mock_parsed.pages = [MagicMock(text="page1", page_number=1)]
        MockParser.return_value.parse.return_value = mock_parsed

        mock_chunk = MagicMock(text="chunk1", page_number=1, chunk_index=0)
        MockChunker.return_value.chunk.return_value = [mock_chunk]

        report = host.ingest_documents("ds", [str(pdf)])
        assert report.total_rows == 1

    @patch("arrow_lake.ingest.chunker.DocumentChunker")
    @patch("arrow_lake.ingest.document.DocumentParser")
    def test_custom_doc_config(self, MockParser: MagicMock, MockChunker: MagicMock, host: _HostWithMixin, tmp_path: Path) -> None:
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")

        doc_config = MagicMock()
        doc_config.chunk_strategy = "fixed"
        doc_config.chunk_size = 500
        doc_config.chunk_overlap = 50
        doc_config.chunk_tokenizer = None
        doc_config.semantic_embedding_model = None
        doc_config.semantic_similarity_threshold = 0.8
        doc_config.semantic_min_chunk_size = 100
        doc_config.max_file_size_mb = 100
        doc_config.store_raw_pdf = True
        doc_config.blob_prefix = "docs/"

        mock_parsed = MagicMock()
        mock_parsed.pages = [MagicMock(text="page text", page_number=1)]
        MockParser.return_value.parse.return_value = mock_parsed

        MockChunker.return_value.chunk.return_value = [
            MagicMock(text="chunk", page_number=1, chunk_index=0),
        ]

        report = host.ingest_documents("ds", [str(pdf)], doc_config=doc_config)
        assert report.total_rows == 1
        MockChunker.assert_called_once_with(
            strategy="fixed", chunk_size=500, chunk_overlap=50,
            tokenizer=None, embedding_model=None,
            similarity_threshold=0.8, min_chunk_size=100,
        )

    @patch("arrow_lake.ingest.chunker.DocumentChunker")
    @patch("arrow_lake.ingest.document.DocumentParser")
    def test_file_too_large(self, MockParser: MagicMock, MockChunker: MagicMock, host: _HostWithMixin, tmp_path: Path) -> None:
        pdf = tmp_path / "big.pdf"
        pdf.write_bytes(b"x" * 200)

        doc_config = MagicMock()
        doc_config.max_file_size_mb = 0  # 0 MB limit
        doc_config.chunk_strategy = "fixed"
        doc_config.chunk_size = 500
        doc_config.chunk_overlap = 50
        doc_config.chunk_tokenizer = None
        doc_config.semantic_embedding_model = None
        doc_config.semantic_similarity_threshold = 0.8
        doc_config.semantic_min_chunk_size = 100

        with pytest.raises(DocumentError, match="exceeds limit"):
            host.ingest_documents("ds", [str(pdf)], doc_config=doc_config)

    @patch("arrow_lake.ingest.chunker.DocumentChunker")
    @patch("arrow_lake.ingest.document.DocumentParser")
    def test_empty_chunks_zero_rows(self, MockParser: MagicMock, MockChunker: MagicMock, host: _HostWithMixin, tmp_path: Path) -> None:
        pdf = tmp_path / "empty.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")

        mock_parsed = MagicMock()
        mock_parsed.pages = []
        MockParser.return_value.parse.return_value = mock_parsed
        MockChunker.return_value.chunk.return_value = []

        report = host.ingest_documents("ds", [str(pdf)])
        assert report.total_rows == 0
        assert report.sources[0].row_count == 0

    @patch("arrow_lake.ingest.chunker.DocumentChunker")
    @patch("arrow_lake.ingest.document.DocumentParser")
    def test_blob_store_upload(self, MockParser: MagicMock, MockChunker: MagicMock, host: _HostWithMixin, tmp_path: Path) -> None:
        pdf = tmp_path / "upload.pdf"
        pdf.write_bytes(b"%PDF-1.4 content")

        mock_parsed = MagicMock()
        mock_parsed.pages = [MagicMock(text="text", page_number=1)]
        MockParser.return_value.parse.return_value = mock_parsed
        MockChunker.return_value.chunk.return_value = [
            MagicMock(text="c1", page_number=1, chunk_index=0),
        ]

        blob_store = MagicMock()
        report = host.ingest_documents("ds", [str(pdf)], blob_store=blob_store)
        blob_store.upload.assert_called_once()
        call_args = blob_store.upload.call_args
        assert "upload.pdf" in call_args[0][0]

    @patch("arrow_lake.ingest.chunker.DocumentChunker")
    @patch("arrow_lake.ingest.document.DocumentParser")
    def test_blob_store_upload_failure(self, MockParser: MagicMock, MockChunker: MagicMock, host: _HostWithMixin, tmp_path: Path) -> None:
        pdf = tmp_path / "fail.pdf"
        pdf.write_bytes(b"%PDF-1.4 content")

        doc_config = MagicMock()
        doc_config.chunk_strategy = "fixed"
        doc_config.chunk_size = 500
        doc_config.chunk_overlap = 50
        doc_config.chunk_tokenizer = None
        doc_config.semantic_embedding_model = None
        doc_config.semantic_similarity_threshold = 0.8
        doc_config.semantic_min_chunk_size = 100
        doc_config.max_file_size_mb = 100
        doc_config.store_raw_pdf = True
        doc_config.blob_prefix = "docs/"

        mock_parsed = MagicMock()
        mock_parsed.pages = [MagicMock(text="text", page_number=1)]
        MockParser.return_value.parse.return_value = mock_parsed
        MockChunker.return_value.chunk.return_value = [
            MagicMock(text="c1", page_number=1, chunk_index=0),
        ]

        blob_store = MagicMock()
        blob_store.upload.side_effect = OSError("disk full")

        with pytest.raises(DocumentError, match="Failed to upload"):
            host.ingest_documents("ds", [str(pdf)], doc_config=doc_config, blob_store=blob_store)

    @patch("arrow_lake.ingest.chunker.DocumentChunker")
    @patch("arrow_lake.ingest.document.DocumentParser")
    def test_document_id_stable(self, MockParser: MagicMock, MockChunker: MagicMock, host: _HostWithMixin, tmp_path: Path) -> None:
        pdf = tmp_path / "stable.pdf"
        pdf.write_bytes(b"%PDF-1.4")

        mock_parsed = MagicMock()
        mock_parsed.pages = [MagicMock(text="t", page_number=1)]
        MockParser.return_value.parse.return_value = mock_parsed
        MockChunker.return_value.chunk.return_value = [
            MagicMock(text="t", page_number=1, chunk_index=0),
        ]

        # Capture the table written to verify document_id
        written_tables: list[pa.Table] = []
        orig_write = host._write_table

        def capture_write(dataset_name, table, sources, source_path):
            written_tables.append(table)
            orig_write(dataset_name, table, sources, source_path)

        host._write_table = capture_write
        host.ingest_documents("ds", [str(pdf)])

        expected_id = hashlib.sha256(str(pdf.resolve()).encode()).hexdigest()[:16]
        assert written_tables[0].column("document_id")[0].as_py() == expected_id

    @patch("arrow_lake.ingest.chunker.DocumentChunker")
    @patch("arrow_lake.ingest.document.DocumentParser")
    def test_safe_stem_sanitization(self, MockParser: MagicMock, MockChunker: MagicMock, host: _HostWithMixin, tmp_path: Path) -> None:
        # Create file with tricky name
        pdf = tmp_path / "my..doc.pdf"
        pdf.write_bytes(b"%PDF-1.4")

        doc_config = MagicMock()
        doc_config.chunk_strategy = "fixed"
        doc_config.chunk_size = 500
        doc_config.chunk_overlap = 50
        doc_config.chunk_tokenizer = None
        doc_config.semantic_embedding_model = None
        doc_config.semantic_similarity_threshold = 0.8
        doc_config.semantic_min_chunk_size = 100
        doc_config.max_file_size_mb = 100
        doc_config.store_raw_pdf = True
        doc_config.blob_prefix = "docs/"

        mock_parsed = MagicMock()
        mock_parsed.pages = [MagicMock(text="t", page_number=1)]
        MockParser.return_value.parse.return_value = mock_parsed
        MockChunker.return_value.chunk.return_value = [
            MagicMock(text="t", page_number=1, chunk_index=0),
        ]

        blob_store = MagicMock()
        host.ingest_documents("ds", [str(pdf)], doc_config=doc_config, blob_store=blob_store)

        blob_key = blob_store.upload.call_args[0][0]
        # ".." in stem should be sanitized to "_" (stem is "my..doc" -> "my_doc")
        assert "my_doc/" in blob_key


# ---------------------------------------------------------------------------
# ingest_join
# ---------------------------------------------------------------------------


class TestIngestJoin:
    @patch("daft.from_arrow")
    def test_basic_join(self, mock_from_arrow: MagicMock, host: _HostWithMixin) -> None:
        left_table = pa.table({"id": [1], "val": ["a"]})
        right_table = pa.table({"id": [1], "name": ["x"]})

        host._manager.read_dataset.side_effect = [left_table, right_table]

        mock_left_df = MagicMock()
        mock_right_df = MagicMock()
        mock_joined = MagicMock()
        mock_count_result = MagicMock()
        mock_count_result.to_arrow.return_value.column(0).__getitem__.return_value.as_py.return_value = 5
        mock_joined.count.return_value = mock_count_result

        mock_from_arrow.side_effect = [mock_left_df, mock_right_df]
        mock_left_df.join.return_value = mock_joined

        report = host.ingest_join("left_ds", right_dataset="right_ds", left_on="id")
        assert report.total_rows == 5
        assert report.sources[0].path == "join:left_ds+right_ds"

    @patch("daft.from_arrow")
    def test_join_with_transforms(self, mock_from_arrow: MagicMock, host: _HostWithMixin) -> None:
        left_table = pa.table({"id": [1]})
        right_table = pa.table({"id": [1]})

        host._manager.read_dataset.side_effect = [left_table, right_table]

        mock_left_df = MagicMock()
        mock_right_df = MagicMock()
        mock_joined = MagicMock()
        mock_count_result = MagicMock()
        mock_count_result.to_arrow.return_value.column(0).__getitem__.return_value.as_py.return_value = 3
        mock_joined.count.return_value = mock_count_result

        mock_from_arrow.side_effect = [mock_left_df, mock_right_df]
        mock_left_df.join.return_value = mock_joined

        t = MagicMock(return_value=mock_joined)
        report = host.ingest_join("left_ds", right_dataset="right_ds", left_on="id", transforms=[t])
        t.assert_called_once_with(mock_joined)

    @patch("daft.from_arrow")
    def test_join_right_on_defaults_to_left_on(self, mock_from_arrow: MagicMock, host: _HostWithMixin) -> None:
        left_table = pa.table({"k": [1]})
        right_table = pa.table({"k": [1]})
        host._manager.read_dataset.side_effect = [left_table, right_table]

        mock_left_df = MagicMock()
        mock_right_df = MagicMock()
        mock_joined = MagicMock()
        mock_count_result = MagicMock()
        mock_count_result.to_arrow.return_value.column(0).__getitem__.return_value.as_py.return_value = 1
        mock_joined.count.return_value = mock_count_result

        mock_from_arrow.side_effect = [mock_left_df, mock_right_df]
        mock_left_df.join.return_value = mock_joined

        host.ingest_join("ds1", right_dataset="ds2", left_on="k")
        mock_left_df.join.assert_called_once_with(
            mock_right_df, left_on="k", right_on="k", how="left", prefix="right_",
        )


# ---------------------------------------------------------------------------
# ingest_union
# ---------------------------------------------------------------------------


class TestIngestUnion:
    @patch("daft.from_arrow")
    def test_basic_union(self, mock_from_arrow: MagicMock, host: _HostWithMixin) -> None:
        t1 = pa.table({"id": [1]})
        t2 = pa.table({"id": [2]})

        host._manager.read_dataset.side_effect = [t1, t2]

        mock_df1 = MagicMock()
        mock_df2 = MagicMock()
        mock_from_arrow.side_effect = [mock_df1, mock_df2]

        mock_union = MagicMock()
        mock_df1.union_all.return_value = mock_union

        mock_count_result = MagicMock()
        mock_count_result.to_arrow.return_value.column(0).__getitem__.return_value.as_py.return_value = 2
        mock_union.count.return_value = mock_count_result

        report = host.ingest_union("merged", source_datasets=["ds1", "ds2"])
        assert report.total_rows == 2
        assert report.sources[0].file_count == 2

    @patch("daft.from_arrow")
    def test_union_with_transforms(self, mock_from_arrow: MagicMock, host: _HostWithMixin) -> None:
        t1 = pa.table({"id": [1]})
        host._manager.read_dataset.return_value = t1

        mock_df = MagicMock()
        mock_from_arrow.return_value = mock_df

        mock_count_result = MagicMock()
        mock_count_result.to_arrow.return_value.column(0).__getitem__.return_value.as_py.return_value = 1
        mock_df.count.return_value = mock_count_result

        t = MagicMock(return_value=mock_df)
        report = host.ingest_union("merged", source_datasets=["ds1"], transforms=[t])
        t.assert_called_once_with(mock_df)


# ---------------------------------------------------------------------------
# _group_by_type
# ---------------------------------------------------------------------------


class TestGroupByType:
    def test_groups_by_extension(self) -> None:
        result = _FileIngestMixin._group_by_type(["/a.csv", "/b.csv", "/c.parquet"])
        assert "csv" in result
        assert "parquet" in result
        assert len(result["csv"]) == 2

    def test_skips_unknown_extensions(self) -> None:
        result = _FileIngestMixin._group_by_type(["/a.txt", "/b.csv"])
        assert "csv" in result
        assert "txt" not in result

    def test_empty_input(self) -> None:
        assert _FileIngestMixin._group_by_type([]) == {}


# ---------------------------------------------------------------------------
# _read_files_df
# ---------------------------------------------------------------------------


class TestReadFilesDf:
    @patch("daft.read_csv")
    def test_csv(self, mock_read_csv: MagicMock) -> None:
        mock_read_csv.return_value = "csv_df"
        result = _FileIngestMixin._read_files_df(["/a.csv"], "csv")
        assert result == "csv_df"

    @patch("daft.read_json")
    def test_json(self, mock_read_json: MagicMock) -> None:
        mock_read_json.return_value = "json_df"
        result = _FileIngestMixin._read_files_df(["/a.json"], "json")
        assert result == "json_df"

    @patch("daft.read_parquet")
    def test_parquet(self, mock_read_parquet: MagicMock) -> None:
        mock_read_parquet.return_value = "pq_df"
        result = _FileIngestMixin._read_files_df(["/a.parquet"], "parquet")
        assert result == "pq_df"

    def test_unsupported_type(self) -> None:
        with pytest.raises(IngestError, match="Batch read unsupported"):
            _FileIngestMixin._read_files_df(["/a.xml"], "xml")
