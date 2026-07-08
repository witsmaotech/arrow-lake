"""File-based ingestion methods for Ingestor.

Handles CSV, JSON/JSONL, Parquet file ingestion, document parsing,
and dataset composition (join, union).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import pyarrow as pa

from arrow_lake.exceptions import ErrorCode, IngestError


class _FileIngestMixin:
    """Mixin providing file, document, and composition ingestion methods.

    Requires the host class to have:
    - ``_manager`` (LanceStorageManager)
    - ``_first_table_seen`` (dict)
    - ``_write_table()`` method
    - ``_build_report()`` static method
    """

    def ingest(
        self,
        dataset_name: str,
        file_paths: list[str],
        *,
        transforms: list[Any] | None = None,
    ) -> Any:
        """Ingest files into a Lance dataset."""
        if not file_paths:
            raise IngestError(
                error_code=ErrorCode.INGEST_UNSUPPORTED_FORMAT,
                message="No file paths provided for ingestion",
            )

        sources: list[Any] = []

        for fp in file_paths:
            ft = self._detect_file_type(fp)
            df = self._read_file_df(fp, ft)
            if transforms:
                for t in transforms:
                    df = t(df)
            table = df.to_arrow()
            self._write_table(dataset_name, table, sources, fp)

        return self._build_report(sources)

    def ingest_batch(
        self,
        dataset_name: str,
        file_paths: list[str],
        *,
        transforms: list[Any] | None = None,
    ) -> Any:
        """Batch-ingest files of the same type."""
        from arrow_lake.ingest.ingestor import IngestionSource

        if not file_paths:
            raise IngestError(
                error_code=ErrorCode.INGEST_UNSUPPORTED_FORMAT,
                message="No file paths provided for batch ingestion",
            )

        sources: list[IngestionSource] = []
        grouped = self._group_by_type(file_paths)

        for file_type, paths in grouped.items():
            df = self._read_files_df(paths, file_type)
            if transforms:
                for t in transforms:
                    df = t(df)
            row_count = df.count().to_arrow()[0].as_py()
            self._manager.write_lance_from_dataframe(
                dataset_name, df, mode="create",
            )
            sources.append(IngestionSource(
                path=f"batch:{file_type}",
                row_count=row_count,
                file_count=len(paths),
            ))

        return self._build_report(sources)

    def ingest_documents(
        self,
        dataset_name: str,
        pdf_paths: list[str],
        *,
        doc_config: Any = None,
        blob_store: Any = None,
        ocr_client: Any = None,
        doc_type: str | None = None,
    ) -> Any:
        """Ingest PDF documents: parse -> chunk -> write to Lance dataset."""
        from arrow_lake.exceptions import DocumentError
        from arrow_lake.exceptions import ErrorCode as DocErrorCode
        from arrow_lake.ingest.chunker import DocumentChunker
        from arrow_lake.ingest.document import DocumentParser
        from arrow_lake.ingest.ingestor import IngestionSource

        if doc_config is not None:
            chunker = DocumentChunker(
                strategy=doc_config.chunk_strategy,
                chunk_size=doc_config.chunk_size,
                chunk_overlap=doc_config.chunk_overlap,
                tokenizer=doc_config.chunk_tokenizer,
                embedding_model=doc_config.semantic_embedding_model,
                similarity_threshold=doc_config.semantic_similarity_threshold,
                min_chunk_size=doc_config.semantic_min_chunk_size,
                docling_chunk_tokenizer=doc_config.docling_chunk_tokenizer,
            )
            parser = DocumentParser(doc_config)
        else:
            chunker = DocumentChunker()
            parser = DocumentParser()

        sources: list[IngestionSource] = []

        for pdf_path_str in pdf_paths:
            pdf_path = Path(pdf_path_str)

            max_size_mb = doc_config.max_file_size_mb if doc_config else 100
            file_size = pdf_path.stat().st_size
            if file_size > max_size_mb * 1024 * 1024:
                raise DocumentError(
                    error_code=DocErrorCode.DOCUMENT_TOO_LARGE,
                    message=f"File '{pdf_path}' ({file_size} bytes) exceeds limit ({max_size_mb}MB)",
                )

            parsed = parser.parse(pdf_path, ocr_client=ocr_client)

            blob_key = ""
            if blob_store is not None and (doc_config is None or doc_config.store_raw_pdf):
                prefix = doc_config.blob_prefix if doc_config else "documents/"
                safe_stem = re.sub(r'[/\\:\0]', '_', pdf_path.stem)
                safe_stem = re.sub(r'\.\.', '_', safe_stem)
                # Sanitize to the blob-allowed charset (alphanumeric, dots, hyphens,
                # underscores). CJK / other non-ASCII in filenames (e.g.
                # "5.芜湖市...pdf") would otherwise fail _BLOB_SEGMENT_RE and break
                # ingest; replace such chars with '_' so the key stays ASCII-safe.
                safe_stem = re.sub(r'[^a-zA-Z0-9._-]', '_', safe_stem)
                if not safe_stem:
                    safe_stem = "doc"
                safe_name = re.sub(r'[^a-zA-Z0-9._-]', '_', pdf_path.name)
                blob_key = f"{prefix}{safe_stem}/{safe_name}"
                try:
                    blob_store.upload(blob_key, pdf_path.read_bytes())
                except OSError as exc:
                    raise DocumentError(
                        error_code=DocErrorCode.DOCUMENT_UPLOAD_FAILED,
                        message=f"Failed to upload '{pdf_path}' to blob store: {exc}",
                    ) from exc

            chunks = chunker.chunk(
                list(parsed.pages),
                docling_doc=getattr(parsed, "docling_doc", None),
            )

            if not chunks:
                sources.append(IngestionSource(
                    path=str(pdf_path),
                    row_count=0,
                    file_count=1,
                ))
                continue

            doc_id = hashlib.sha256(str(pdf_path.resolve()).encode()).hexdigest()[:16]
            # v1.7: resolve per-file doc_type — explicit (caller) > auto-classify > "".
            resolved_doc_type = doc_type or ""
            _classifier = getattr(self, "_doc_type_classifier", None)
            if not resolved_doc_type and _classifier is not None:
                try:
                    inferred = _classifier.classify_sync(
                        getattr(parsed, "text", "") or ""
                    )
                    if inferred:
                        resolved_doc_type = inferred
                except Exception as exc:  # noqa: BLE001 — best-effort, never block ingest
                    import structlog
                    structlog.get_logger(__name__).warning(
                        "ingest.doc_type_classify_failed",
                        path=str(pdf_path), err=str(exc)[:120],
                    )
            rows = {
                "text": [c.text for c in chunks],
                "page_number": [c.page_number for c in chunks],
                "chunk_index": [c.chunk_index for c in chunks],
                "document_id": [doc_id] * len(chunks),
                "blob_key": [blob_key] * len(chunks),
                "doc_type": [resolved_doc_type] * len(chunks),
            }
            table = pa.table(rows)

            self._write_table(dataset_name, table, sources, str(pdf_path))

        return self._build_report(sources)

    def ingest_join(
        self,
        dataset_name: str,
        *,
        right_dataset: str,
        left_on: str,
        right_on: str | None = None,
        how: str = "left",
        transforms: list[Any] | None = None,
    ) -> Any:
        """Join current dataset with another Lance dataset and write result."""
        import daft

        from arrow_lake.ingest.ingestor import IngestionSource

        right_on = right_on or left_on
        left_table = self._manager.read_dataset(dataset_name)
        right_table = self._manager.read_dataset(right_dataset)
        left_df = daft.from_arrow(left_table)
        right_df = daft.from_arrow(right_table)

        df = left_df.join(right_df, left_on=left_on, right_on=right_on, how=how, prefix="right_")
        if transforms:
            for t in transforms:
                df = t(df)
        row_count = df.count().to_arrow().column(0)[0].as_py()
        self._manager.write_lance_from_dataframe(
            f"{dataset_name}_joined", df, mode="create",
        )
        return self._build_report([IngestionSource(
            path=f"join:{dataset_name}+{right_dataset}", row_count=row_count, file_count=1,
        )])

    def ingest_union(
        self,
        dataset_name: str,
        *,
        source_datasets: list[str],
        transforms: list[Any] | None = None,
    ) -> Any:
        """Union multiple Lance datasets and write result."""
        import daft

        from arrow_lake.ingest.ingestor import IngestionSource

        dfs = []
        for src in source_datasets:
            table = self._manager.read_dataset(src)
            dfs.append(daft.from_arrow(table))
        df = dfs[0]
        for other in dfs[1:]:
            df = df.union_all(other)

        if transforms:
            for t in transforms:
                df = t(df)
        row_count = df.count().to_arrow().column(0)[0].as_py()
        self._manager.write_lance_from_dataframe(dataset_name, df, mode="create")
        return self._build_report([IngestionSource(
            path=f"union:{','.join(source_datasets)}", row_count=row_count,
            file_count=len(source_datasets),
        )])

    @staticmethod
    def _group_by_type(file_paths: list[str]) -> dict[str, list[str]]:
        """Group file paths by their detected type."""
        from arrow_lake.ingest.ingestor import Ingestor

        groups: dict[str, list[str]] = {}
        for fp in file_paths:
            ext = Path(fp).suffix.lower()
            ft = Ingestor._SUPPORTED_EXTENSIONS.get(ext, "")
            if not ft:
                continue
            groups.setdefault(ft, []).append(fp)
        return groups

    @staticmethod
    def _read_files_df(
        paths: list[str],
        file_type: str,
    ) -> Any:
        """Read multiple files of the same type into a single Daft DataFrame."""
        import daft

        if file_type == "csv":
            return daft.read_csv(paths)
        elif file_type == "json":
            return daft.read_json(paths)
        elif file_type == "parquet":
            return daft.read_parquet(paths)
        raise IngestError(
            error_code=ErrorCode.INGEST_UNSUPPORTED_FORMAT,
            message=f"Batch read unsupported for type: {file_type}",
        )
