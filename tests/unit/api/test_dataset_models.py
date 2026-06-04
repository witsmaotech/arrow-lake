"""Targeted tests for api/models/dataset.py — uncovered validation paths."""

from __future__ import annotations

import pytest

from arrow_lake.api.models.dataset import (
    IngestDocumentsRequest,
    IngestHttpRequest,
    IngestImagesRequest,
    IngestMixedRequest,
    IngestVideosRequest,
)


class TestIngestHttpRequestValidation:
    def test_non_http_scheme_rejected(self) -> None:
        with pytest.raises(ValueError, match="http/https"):
            IngestHttpRequest(urls=["ftp://evil.com/file"])

    def test_url_without_hostname_rejected(self) -> None:
        with pytest.raises(ValueError, match="hostname"):
            IngestHttpRequest(urls=["http://"])


class TestIngestImagesRequestValidation:
    def test_blob_key_must_start_with_uploads(self) -> None:
        with pytest.raises(ValueError, match="uploads/"):
            IngestImagesRequest(blob_keys=["data/image.png"])

    def test_at_least_one_source_required(self) -> None:
        with pytest.raises(ValueError, match="At least one"):
            IngestImagesRequest()


class TestIngestVideosRequestValidation:
    def test_blob_key_must_start_with_uploads(self) -> None:
        with pytest.raises(ValueError, match="uploads/"):
            IngestVideosRequest(blob_keys=["video/clip.mp4"])

    def test_at_least_one_source_required(self) -> None:
        with pytest.raises(ValueError, match="At least one"):
            IngestVideosRequest()


class TestIngestMixedRequestValidation:
    def test_unknown_modality_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unknown modality"):
            IngestMixedRequest(sources={"audio": ["test.mp3"]})

    def test_too_many_paths_rejected(self) -> None:
        with pytest.raises(ValueError, match="Too many"):
            IngestMixedRequest(sources={"files": [f"f{i}" for i in range(101)]})

    def test_blob_key_must_start_with_uploads(self) -> None:
        with pytest.raises(ValueError, match="uploads/"):
            IngestMixedRequest(blob_keys={"images": ["notuploads/img.png"]})

    def test_at_least_one_source_required(self) -> None:
        with pytest.raises(ValueError, match="At least one"):
            IngestMixedRequest()


class TestIngestDocumentsRequestValidation:
    def test_absolute_pdf_path_rejected(self) -> None:
        with pytest.raises(ValueError, match="Absolute paths"):
            IngestDocumentsRequest(pdf_paths=["/tmp/doc.pdf"])

    def test_blob_key_must_start_with_uploads(self) -> None:
        with pytest.raises(ValueError, match="uploads/"):
            IngestDocumentsRequest(blob_keys=["docs/file.pdf"])
