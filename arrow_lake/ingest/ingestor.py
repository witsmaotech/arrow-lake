"""Data ingestion — Stories 3.1-3.5.

Ingests local files (CSV, JSONL, Parquet), HTTP URLs, images, and videos
into Lance datasets. Supports unified multimodal table ingestion.
Uses Daft for file reading and LanceStorageManager for writing.
"""

from __future__ import annotations

import contextlib
import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import pyarrow as pa

from arrow_lake.exceptions import ErrorCode, IngestError


@dataclass(frozen=True)
class IngestionSource:
    """Stats for a single ingestion source."""

    path: str
    row_count: int
    file_count: int = 1


@dataclass(frozen=True)
class IngestionReport:
    """Result of an ingestion operation."""

    sources: tuple[IngestionSource, ...] = ()
    total_rows: int = 0
    total_files: int = 0


class Ingestor:
    """Ingests files into Lance datasets.

    Supports CSV, JSON/JSONL, Parquet, HTTP URLs, images, and videos.
    Uses Daft for efficient file reading.

    Thread safety: This class is NOT thread-safe for concurrent ingestion
    into the SAME dataset (create/append race). Use separate Ingestor
    instances or external synchronization for concurrent writes.

    Args:
        manager: LanceStorageManager for dataset writes.
    """

    _SUPPORTED_EXTENSIONS: ClassVar[dict[str, str]] = {
        ".csv": "csv",
        ".json": "json",
        ".jsonl": "json",
        ".parquet": "parquet",
    }

    def __init__(self, manager: Any) -> None:
        self._manager = manager
        self._first_table_seen: dict[str, bool] = {}

    def _write_table(
        self,
        dataset_name: str,
        table: pa.Table,
        sources: list[IngestionSource],
        source_path: str,
    ) -> None:
        """Write a table to the dataset (create or append) and track the source."""
        if not self._first_table_seen.get(dataset_name, False):
            self._manager.create_dataset(dataset_name, table)
            self._first_table_seen[dataset_name] = True
        else:
            self._manager.append_dataset(dataset_name, table)

        sources.append(IngestionSource(
            path=source_path,
            row_count=table.num_rows,
            file_count=1,
        ))

    @staticmethod
    def _build_report(sources: list[IngestionSource]) -> IngestionReport:
        """Build an IngestionReport from a list of sources."""
        return IngestionReport(
            sources=tuple(sources),
            total_rows=sum(s.row_count for s in sources),
            total_files=sum(s.file_count for s in sources),
        )

    def ingest(
        self,
        dataset_name: str,
        file_paths: list[str],
        *,
        transforms: list[Any] | None = None,
    ) -> IngestionReport:
        """Ingest files into a Lance dataset.

        Args:
            dataset_name: Target dataset name.
            file_paths: List of file paths to ingest.
            transforms: Optional list of ``daft.DataFrame -> daft.DataFrame``
                        callables applied before Arrow conversion.

        Returns:
            IngestionReport with per-source and total stats.

        Raises:
            IngestError: If ingestion fails or file_paths is empty.
        """
        if not file_paths:
            raise IngestError(
                error_code=ErrorCode.INGEST_UNSUPPORTED_FORMAT,
                message="No file paths provided for ingestion",
            )

        sources: list[IngestionSource] = []

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
    ) -> IngestionReport:
        """Batch-ingest files of the same type, writing once via Daft write_lance.

        Groups files by type, reads each group into a single Daft DataFrame,
        applies transforms, then writes directly to Lance — skipping per-file
        Arrow conversion.

        Args:
            dataset_name: Target dataset name.
            file_paths: List of file paths to ingest.
            transforms: Optional Daft transform callables.

        Returns:
            IngestionReport with per-group stats.

        Raises:
            IngestError: If file_paths is empty or all files are unsupported.
        """
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

    @staticmethod
    def _group_by_type(file_paths: list[str]) -> dict[str, list[str]]:
        """Group file paths by their detected type."""
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

    def ingest_sql(
        self,
        dataset_name: str,
        *,
        sql: str,
        connection_url: str,
        partition_col: str | None = None,
        num_partitions: int | None = None,
        transforms: list[Any] | None = None,
    ) -> IngestionReport:
        """Ingest data from a SQL database query.

        Args:
            dataset_name: Target dataset name.
            sql: SELECT query to execute.
            connection_url: SQLAlchemy connection string.
            partition_col: Optional partition column for parallel reads.
            num_partitions: Number of read partitions.
            transforms: Optional Daft transform callables.

        Returns:
            IngestionReport with stats.

        Raises:
            IngestError: If query fails or contains forbidden statements.
        """
        from arrow_lake.ingest.connectors_sql import SqlConnector

        connector = SqlConnector(
            connection_url,
            partition_col=partition_col,
            num_partitions=num_partitions,
        )
        df = connector.read(sql)
        if transforms:
            for t in transforms:
                df = t(df)
        row_count = df.count().to_arrow().column(0)[0].as_py()
        self._manager.write_lance_from_dataframe(dataset_name, df, mode="create")

        sources = [IngestionSource(
            path=f"sql:{connection_url.split('@')[-1] if '@' in connection_url else connection_url}",
            row_count=row_count,
            file_count=1,
        )]
        return self._build_report(sources)

    def ingest_kafka(
        self,
        dataset_name: str,
        *,
        bootstrap_servers: str,
        topics: list[str] | str,
        start: str = "earliest",
        end: str = "latest",
        json_decode: bool = True,
        transforms: list[Any] | None = None,
    ) -> IngestionReport:
        """Ingest messages from Kafka topics into a Lance dataset.

        Args:
            dataset_name: Target dataset name.
            bootstrap_servers: Kafka broker addresses.
            topics: Topic name(s) to read from.
            start: Start bound (default "earliest").
            end: End bound (default "latest").
            json_decode: Auto-decode JSON message values (default True).
            transforms: Optional Daft transform callables.

        Returns:
            IngestionReport with stats.
        """
        import daft

        from arrow_lake.ingest.connectors_kafka import KafkaConnector

        connector = KafkaConnector(bootstrap_servers)
        df = connector.read(topics=topics, start=start, end=end)

        if json_decode:
            df = df.with_column("value", daft.functions.json_decode(daft.col("value")))
            # Explode struct columns from JSON
            value_type = df.schema()["value"].dtype
            if hasattr(value_type, "fields"):
                for field_name in value_type.fields:
                    df = df.with_column(
                        field_name, daft.col("value")[field_name],
                    )
                df = df.exclude("value")

        if transforms:
            for t in transforms:
                df = t(df)

        row_count = df.count().to_arrow().column(0)[0].as_py()
        self._manager.write_lance_from_dataframe(dataset_name, df, mode="create")

        topic_str = topics if isinstance(topics, str) else ",".join(topics)
        sources = [IngestionSource(
            path=f"kafka:{topic_str}",
            row_count=row_count,
            file_count=1,
        )]
        return self._build_report(sources)

    def ingest_iceberg(
        self,
        dataset_name: str,
        *,
        table_uri: str,
        transforms: list[Any] | None = None,
    ) -> IngestionReport:
        """Ingest data from an Apache Iceberg table.

        Args:
            dataset_name: Target dataset name.
            table_uri: Iceberg table URI.
            transforms: Optional Daft transform callables.

        Returns:
            IngestionReport with stats.
        """
        from arrow_lake.ingest.connectors_lakehouse import IcebergConnector

        connector = IcebergConnector(table_uri)
        df = connector.read()
        if transforms:
            for t in transforms:
                df = t(df)
        row_count = df.count().to_arrow().column(0)[0].as_py()
        self._manager.write_lance_from_dataframe(dataset_name, df, mode="create")
        return self._build_report([IngestionSource(
            path=f"iceberg:{table_uri}", row_count=row_count, file_count=1,
        )])

    def ingest_deltalake(
        self,
        dataset_name: str,
        *,
        table_uri: str,
        version: int | None = None,
        transforms: list[Any] | None = None,
    ) -> IngestionReport:
        """Ingest data from a Delta Lake table.

        Args:
            dataset_name: Target dataset name.
            table_uri: Delta Lake table URI.
            version: Optional table version to read.
            transforms: Optional Daft transform callables.

        Returns:
            IngestionReport with stats.
        """
        from arrow_lake.ingest.connectors_lakehouse import DeltaConnector

        connector = DeltaConnector(table_uri, version=version)
        df = connector.read()
        if transforms:
            for t in transforms:
                df = t(df)
        row_count = df.count().to_arrow().column(0)[0].as_py()
        self._manager.write_lance_from_dataframe(dataset_name, df, mode="create")
        return self._build_report([IngestionSource(
            path=f"delta:{table_uri}", row_count=row_count, file_count=1,
        )])

    def ingest_http(
        self,
        dataset_name: str,
        urls: list[str],
    ) -> IngestionReport:
        """Ingest files from HTTP(S) URLs (Story 3.2).

        Args:
            dataset_name: Target dataset name.
            urls: List of HTTP(S) URLs to fetch and ingest.

        Returns:
            IngestionReport with per-source and total stats.

        Raises:
            IngestError: If fetching or ingestion fails.
        """
        from arrow_lake.ingest.connectors_http import HttpConnector

        connector = HttpConnector()
        sources: list[IngestionSource] = []

        def _fetch_and_read(url: str) -> tuple[str, pa.Table]:
            result = connector.fetch(url)
            ft = self._detect_file_type(url)
            table = self._read_bytes(result.content, ft)
            return result.url, table

        workers = min(len(urls), 4)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_list = [pool.submit(_fetch_and_read, url) for url in urls]
            for future in future_list:
                resolved_url, table = future.result()
                self._write_table(dataset_name, table, sources, resolved_url)

        return self._build_report(sources)

    def ingest_images(
        self,
        dataset_name: str,
        image_paths: list[str],
    ) -> IngestionReport:
        """Ingest image files with thumbnails and EXIF (Story 3.3).

        Args:
            dataset_name: Target dataset name.
            image_paths: List of image file paths.

        Returns:
            IngestionReport with per-source and total stats.

        Raises:
            IngestError: If processing or ingestion fails.
        """
        from arrow_lake.ingest.media import ImageProcessor

        processor = ImageProcessor()
        sources: list[IngestionSource] = []

        for img_path in image_paths:
            result = processor.process(img_path)
            row = {
                "image_data": result.original_bytes,
                "image_thumbnail": result.thumbnail_bytes,
                "image_preview": result.preview_bytes,
                "image_width": result.metadata.width,
                "image_height": result.metadata.height,
                "exif_make": result.metadata.exif_make,
                "exif_model": result.metadata.exif_model,
            }
            table = pa.table({k: [v] for k, v in row.items()})
            self._write_table(dataset_name, table, sources, str(img_path))

        return self._build_report(sources)

    def ingest_videos(
        self,
        dataset_name: str,
        video_paths: list[str],
    ) -> IngestionReport:
        """Ingest video files with keyframe extraction (Story 3.4).

        Args:
            dataset_name: Target dataset name.
            video_paths: List of video file paths.

        Returns:
            IngestionReport with per-source and total stats.

        Raises:
            IngestError: If processing or ingestion fails.
        """
        from arrow_lake.ingest.media import VideoProcessor

        processor = VideoProcessor()
        sources: list[IngestionSource] = []

        for vid_path in video_paths:
            result = processor.extract_keyframes(vid_path)
            row = {
                "video_data": result.keyframes[0].jpeg_bytes if result.keyframes else None,
                "keyframe_count": len(result.keyframes),
                "video_duration_ms": result.duration_ms,
            }
            table = pa.table({k: [v] for k, v in row.items()})
            self._write_table(dataset_name, table, sources, str(vid_path))

        return self._build_report(sources)

    def ingest_mixed(
        self,
        dataset_name: str,
        sources: dict[str, list[str]],
    ) -> IngestionReport:
        """Ingest mixed modality sources into a unified table (Story 3.5).

        Args:
            dataset_name: Target dataset name.
            sources: Dict mapping modality to list of paths/URLs.
                     Supported keys: "files", "urls", "images", "videos".

        Returns:
            Combined IngestionReport.

        Raises:
            IngestError: If any ingestion fails.
        """
        from arrow_lake.ingest.schema import UnifiedTableManager

        mgr = UnifiedTableManager(self._manager)
        mgr.create(dataset_name)

        report_sources: list[IngestionSource] = []
        total_rows = 0
        total_files = 0

        if "files" in sources:
            r = self.ingest(dataset_name, sources["files"])
            report_sources.extend(r.sources)
            total_rows += r.total_rows
            total_files += r.total_files

        if "urls" in sources:
            r = self.ingest_http(dataset_name, sources["urls"])
            report_sources.extend(r.sources)
            total_rows += r.total_rows
            total_files += r.total_files

        if "images" in sources:
            r = self.ingest_images(dataset_name, sources["images"])
            report_sources.extend(r.sources)
            total_rows += r.total_rows
            total_files += r.total_files

        if "videos" in sources:
            r = self.ingest_videos(dataset_name, sources["videos"])
            report_sources.extend(r.sources)
            total_rows += r.total_rows
            total_files += r.total_files

        return IngestionReport(
            sources=tuple(report_sources),
            total_rows=total_rows,
            total_files=total_files,
        )

    def ingest_join(
        self,
        dataset_name: str,
        *,
        right_dataset: str,
        left_on: str,
        right_on: str | None = None,
        how: str = "left",
        transforms: list[Any] | None = None,
    ) -> IngestionReport:
        """Join current dataset with another Lance dataset and write result.

        Args:
            dataset_name: Left dataset (also target for result).
            right_dataset: Right dataset name.
            left_on: Join key on the left side.
            right_on: Join key on the right side (defaults to left_on).
            how: Join type — "left", "right", "inner", "outer".
            transforms: Optional transforms after join.

        Returns:
            IngestionReport with stats.
        """
        import daft

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
    ) -> IngestionReport:
        """Union multiple Lance datasets and write result.

        Args:
            dataset_name: Target dataset name for the union result.
            source_datasets: List of dataset names to union.
            transforms: Optional transforms after union.

        Returns:
            IngestionReport with stats.
        """
        import daft

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

    def ingest_documents(
        self,
        dataset_name: str,
        pdf_paths: list[str],
        *,
        doc_config: Any = None,
        blob_store: Any = None,
        ocr_client: Any = None,
    ) -> IngestionReport:
        """Ingest PDF documents: parse → chunk → write to Lance dataset.

        Data flow:
        1. Parse document (Kreuzberg / TurboOCR)
        2. Upload raw PDF to BlobStore (optional)
        3. Chunk extracted text
        4. Write chunks as Lance rows with metadata

        Args:
            dataset_name: Target dataset name.
            pdf_paths: List of PDF file paths.
            doc_config: DocumentConfig for parsing/chunking options.
            blob_store: BlobStoreManager for raw PDF storage (None = skip upload).
            ocr_client: TurboOcrClient for scanned PDF OCR (None = Kreuzberg only).

        Returns:
            IngestionReport with per-document and total stats.

        Raises:
            DocumentError: If document parsing fails.
            IngestError: If dataset write fails.
        """
        from arrow_lake.exceptions import DocumentError
        from arrow_lake.exceptions import ErrorCode as DocErrorCode
        from arrow_lake.ingest.chunker import DocumentChunker
        from arrow_lake.ingest.document import DocumentParser

        if doc_config is not None:
            chunker = DocumentChunker(
                strategy=doc_config.chunk_strategy,
                chunk_size=doc_config.chunk_size,
                chunk_overlap=doc_config.chunk_overlap,
                tokenizer=doc_config.chunk_tokenizer,
                embedding_model=doc_config.semantic_embedding_model,
                similarity_threshold=doc_config.semantic_similarity_threshold,
                min_chunk_size=doc_config.semantic_min_chunk_size,
            )
            parser = DocumentParser(doc_config)
        else:
            chunker = DocumentChunker()
            parser = DocumentParser()

        sources: list[IngestionSource] = []

        for pdf_path_str in pdf_paths:
            pdf_path = Path(pdf_path_str)

            # Validate file size
            max_size_mb = doc_config.max_file_size_mb if doc_config else 100
            file_size = pdf_path.stat().st_size
            if file_size > max_size_mb * 1024 * 1024:
                raise DocumentError(
                    error_code=DocErrorCode.DOCUMENT_TOO_LARGE,
                    message=f"File '{pdf_path}' ({file_size} bytes) exceeds limit ({max_size_mb}MB)",
                )

            # Parse document
            parsed = parser.parse(pdf_path, ocr_client=ocr_client)

            # Upload raw PDF to BlobStore
            blob_key = ""
            if blob_store is not None and (doc_config is None or doc_config.store_raw_pdf):
                prefix = doc_config.blob_prefix if doc_config else "documents/"
                safe_stem = re.sub(r'[/\\:\0]', '_', pdf_path.stem)
                safe_stem = re.sub(r'\.\.', '_', safe_stem)
                if not safe_stem:
                    safe_stem = "doc"
                blob_key = f"{prefix}{safe_stem}/{pdf_path.name}"
                try:
                    blob_store.upload(blob_key, pdf_path.read_bytes())
                except OSError as exc:
                    raise DocumentError(
                        error_code=DocErrorCode.DOCUMENT_UPLOAD_FAILED,
                        message=f"Failed to upload '{pdf_path}' to blob store: {exc}",
                    ) from exc

            # Chunk
            chunks = chunker.chunk(list(parsed.pages))

            if not chunks:
                sources.append(IngestionSource(
                    path=str(pdf_path),
                    row_count=0,
                    file_count=1,
                ))
                continue

            # Build Arrow table from chunks
            doc_id = hashlib.sha256(str(pdf_path.resolve()).encode()).hexdigest()[:16]
            rows = {
                "text": [c.text for c in chunks],
                "page_number": [c.page_number for c in chunks],
                "chunk_index": [c.chunk_index for c in chunks],
                "document_id": [doc_id] * len(chunks),
                "blob_key": [blob_key] * len(chunks),
            }
            table = pa.table(rows)

            self._write_table(dataset_name, table, sources, str(pdf_path))

        return self._build_report(sources)

    @classmethod
    def _detect_file_type(cls, path: Path | str) -> str:
        """Detect file type from extension.

        Args:
            path: File path (local, S3 URI, or URL).

        Returns:
            File type string ('csv', 'json', 'parquet').

        Raises:
            IngestError: If extension is not supported.
        """
        p = str(path).lower()
        for ext, ft in cls._SUPPORTED_EXTENSIONS.items():
            if p.endswith(ext):
                return ft
        raise IngestError(
            error_code=ErrorCode.INGEST_UNSUPPORTED_FORMAT,
            message=f"Unsupported file format for '{path}'",
        )

    @staticmethod
    def _read_file_df(
        path: Path | str,
        file_type: str,
        *,
        columns: list[str] | None = None,
    ) -> Any:
        """Read a file into a Daft DataFrame (lazy, not yet materialized).

        Args:
            path: File path (local or s3:// URI).
            file_type: File type string.
            columns: Optional column subset to read (column pruning).

        Returns:
            daft.DataFrame for further lazy operations.

        Raises:
            IngestError: If file cannot be read.
        """
        import daft

        try:
            sp = str(path)
            read_kwargs: dict[str, Any] = {}
            if columns and file_type in ("csv", "parquet"):
                read_kwargs["columns"] = columns

            if file_type == "csv":
                return daft.read_csv(sp, **read_kwargs)
            elif file_type == "json":
                return daft.read_json(sp)
            elif file_type == "parquet":
                return daft.read_parquet(sp, **read_kwargs)
            else:
                raise IngestError(
                    error_code=ErrorCode.INGEST_UNSUPPORTED_FORMAT,
                    message=f"Unsupported file type: {file_type}",
                )
        except IngestError:
            raise
        except (ImportError, OSError, ValueError) as exc:
            raise IngestError(
                error_code=ErrorCode.INGEST_FILE_NOT_FOUND,
                message=f"Failed to read '{path}': {exc}",
            ) from exc

    @staticmethod
    def _read_file(
        path: Path | str,
        file_type: str,
        *,
        columns: list[str] | None = None,
    ) -> pa.Table:
        """Read a file into an Arrow Table using Daft.

        Thin wrapper around ``_read_file_df().to_arrow()``.
        """
        df = Ingestor._read_file_df(path, file_type, columns=columns)
        return df.to_arrow()

    @staticmethod
    def _read_bytes(content: bytes, file_type: str) -> pa.Table:
        """Read raw bytes into an Arrow Table based on detected type.

        Args:
            content: Raw file content bytes.
            file_type: File type string ('csv', 'json', 'parquet').

        Returns:
            Arrow Table with content.

        Raises:
            IngestError: If reading fails.
        """
        import tempfile

        import daft

        try:
            suffix = f".{file_type}"
            fd, tmp_path = tempfile.mkstemp(suffix=suffix)
            try:
                import os

                os.write(fd, content)
                os.close(fd)
                if file_type == "json":
                    df = daft.read_json(tmp_path)
                elif file_type == "csv":
                    df = daft.read_csv(tmp_path)
                elif file_type == "parquet":
                    df = daft.read_parquet(tmp_path)
                else:
                    raise IngestError(
                        error_code=ErrorCode.INGEST_UNSUPPORTED_FORMAT,
                        message=f"Unsupported file type: {file_type}",
                    )
                return df.to_arrow()
            finally:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)
        except IngestError:
            raise
        except (ImportError, OSError, ValueError) as exc:
            raise IngestError(
                error_code=ErrorCode.INGEST_FILE_NOT_FOUND,
                message=f"Failed to read content: {exc}",
            ) from exc
