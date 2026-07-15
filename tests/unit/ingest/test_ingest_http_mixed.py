"""Tests for HTTP ingestion — Story 3.2 (unit).

Tests HttpConnector with mocked httpx responses:
- URL scheme validation (SSRF prevention)
- Retry on transient errors
- Error code mapping (4xx, timeout, rate limit)
- Content detection from Content-Type
- Ingestor Sprint 3 methods (ingest_http, ingest_images, ingest_videos, ingest_mixed)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest
from arrow_lake.exceptions import ErrorCode, HttpError


class TestHttpConnectorUrlValidation:
    """Test URL scheme validation for SSRF prevention."""

    def test_http_url_accepted(self) -> None:
        from arrow_lake.ingest.connectors_http import HttpConnector

        connector = HttpConnector(timeout_seconds=5.0)
        # Only checking URL validation logic, not actual fetch
        assert connector._validate_url("http://example.com/data.csv")

    def test_https_url_accepted(self) -> None:
        from arrow_lake.ingest.connectors_http import HttpConnector

        connector = HttpConnector(timeout_seconds=5.0)
        assert connector._validate_url("https://example.com/data.csv")

    def test_file_url_rejected(self) -> None:
        from arrow_lake.ingest.connectors_http import HttpConnector

        connector = HttpConnector(timeout_seconds=5.0)
        with pytest.raises(HttpError, match="scheme"):
            connector._validate_url("file:///etc/passwd")

    def test_ftp_url_rejected(self) -> None:
        from arrow_lake.ingest.connectors_http import HttpConnector

        connector = HttpConnector(timeout_seconds=5.0)
        with pytest.raises(HttpError, match="scheme"):
            connector._validate_url("ftp://evil.com/data")

    def test_no_scheme_rejected(self) -> None:
        from arrow_lake.ingest.connectors_http import HttpConnector

        connector = HttpConnector(timeout_seconds=5.0)
        with pytest.raises(HttpError, match="scheme"):
            connector._validate_url("//example.com/data.csv")


class TestHttpConnectorErrorMapping:
    """Test HTTP error code to ErrorCode mapping."""

    def test_4xx_maps_to_fetch_failed(self) -> None:
        from arrow_lake.ingest.connectors_http import HttpConnector

        connector = HttpConnector(timeout_seconds=5.0)
        code = connector._map_status_code(404)
        assert code == ErrorCode.HTTP_FETCH_FAILED

    def test_400_maps_to_fetch_failed(self) -> None:
        from arrow_lake.ingest.connectors_http import HttpConnector

        connector = HttpConnector(timeout_seconds=5.0)
        code = connector._map_status_code(400)
        assert code == ErrorCode.HTTP_FETCH_FAILED

    def test_408_timeout_maps_correctly(self) -> None:
        from arrow_lake.ingest.connectors_http import HttpConnector

        connector = HttpConnector(timeout_seconds=5.0)
        code = connector._map_status_code(408)
        assert code == ErrorCode.HTTP_TIMEOUT

    def test_504_timeout_maps_correctly(self) -> None:
        from arrow_lake.ingest.connectors_http import HttpConnector

        connector = HttpConnector(timeout_seconds=5.0)
        code = connector._map_status_code(504)
        assert code == ErrorCode.HTTP_TIMEOUT

    def test_429_rate_limited(self) -> None:
        from arrow_lake.ingest.connectors_http import HttpConnector

        connector = HttpConnector(timeout_seconds=5.0)
        code = connector._map_status_code(429)
        assert code == ErrorCode.HTTP_RATE_LIMITED

    def test_200_is_none(self) -> None:
        from arrow_lake.ingest.connectors_http import HttpConnector

        connector = HttpConnector(timeout_seconds=5.0)
        assert connector._map_status_code(200) is None


class TestHttpConnectorFetchResult:
    """Test HttpFetchResult data structure."""

    def test_fetch_result_is_frozen(self) -> None:
        from arrow_lake.ingest.connectors_http import HttpFetchResult

        result = HttpFetchResult(
            url="http://example.com/data.csv",
            content=b"col1,col2\n1,2",
            content_type="text/csv",
            status_code=200,
        )
        with pytest.raises(AttributeError):
            result.url = "other"  # type: ignore[misc]


class TestHttpConnectorListFiles:
    """Test list_files method of HttpConnector."""

    def test_list_files_returns_connector_result(self) -> None:
        from arrow_lake.ingest.connectors_http import HttpConnector

        connector = HttpConnector(timeout_seconds=5.0)
        result = connector.list_files()
        # HttpConnector.list_files() returns empty ConnectorResult
        # (file discovery is done via fetch, not list)
        assert result.file_count == 0

    def test_list_files_with_extensions(self) -> None:
        from arrow_lake.ingest.connectors_http import HttpConnector

        connector = HttpConnector(timeout_seconds=5.0)
        result = connector.list_files(extensions=[".csv"])
        assert result.file_count == 0


class TestHttpConnectorSsrfPrevention:
    """Test SSRF prevention via private IP blocking."""

    def test_loopback_blocked(self) -> None:
        from arrow_lake.ingest.connectors_http import HttpConnector

        connector = HttpConnector(timeout_seconds=5.0)
        with pytest.raises(HttpError, match="private IP"):
            connector._validate_url("http://127.0.0.1/admin")

    def test_rfc1918_class_a_blocked(self) -> None:
        from arrow_lake.ingest.connectors_http import HttpConnector

        connector = HttpConnector(timeout_seconds=5.0)
        with pytest.raises(HttpError, match="private IP"):
            connector._validate_url("http://10.0.0.1/secret")

    def test_rfc1918_class_b_blocked(self) -> None:
        from arrow_lake.ingest.connectors_http import HttpConnector

        connector = HttpConnector(timeout_seconds=5.0)
        with pytest.raises(HttpError, match="private IP"):
            connector._validate_url("http://172.16.0.1/data")

    def test_rfc1918_class_c_blocked(self) -> None:
        from arrow_lake.ingest.connectors_http import HttpConnector

        connector = HttpConnector(timeout_seconds=5.0)
        with pytest.raises(HttpError, match="private IP"):
            connector._validate_url("http://192.168.1.1/local")

    def test_link_local_blocked(self) -> None:
        from arrow_lake.ingest.connectors_http import HttpConnector

        connector = HttpConnector(timeout_seconds=5.0)
        with pytest.raises(HttpError, match="private IP"):
            connector._validate_url("http://169.254.169.254/metadata")

    def test_ipv6_loopback_blocked(self) -> None:
        from arrow_lake.ingest.connectors_http import HttpConnector

        connector = HttpConnector(timeout_seconds=5.0)
        with pytest.raises(HttpError, match="private IP"):
            connector._validate_url("http://[::1]/admin")

    def test_ipv6_unique_local_blocked(self) -> None:
        from arrow_lake.ingest.connectors_http import HttpConnector

        connector = HttpConnector(timeout_seconds=5.0)
        with pytest.raises(HttpError, match="private IP"):
            connector._validate_url("http://[fc00::1]/data")

    def test_ipv6_link_local_blocked(self) -> None:
        from arrow_lake.ingest.connectors_http import HttpConnector

        connector = HttpConnector(timeout_seconds=5.0)
        with pytest.raises(HttpError, match="private IP"):
            connector._validate_url("http://[fe80::1]/local")

    def test_public_domain_allowed(self) -> None:
        from arrow_lake.ingest.connectors_http import HttpConnector

        connector = HttpConnector(timeout_seconds=5.0)
        assert connector._validate_url("https://example.com/data.csv")


class TestHttpConnectorRetry:
    """Test retry behavior."""

    def test_build_retry_decorator(self) -> None:
        from arrow_lake.ingest.connectors_http import HttpConnector

        connector = HttpConnector(timeout_seconds=5.0, max_retries=5)
        decorator = connector._build_retry_decorator()
        assert decorator is not None

    def test_fetch_with_mock_success(self) -> None:
        from unittest.mock import MagicMock

        from arrow_lake.ingest.connectors_http import HttpConnector, HttpFetchResult

        connector = HttpConnector(timeout_seconds=5.0, max_retries=1)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b'{"key": "value"}'
        mock_response.headers = {"content-type": "application/json"}
        mock_response.text = ""

        connector._client = MagicMock()
        connector._client.get.return_value = mock_response

        result = connector.fetch("https://example.com/data.json")
        assert isinstance(result, HttpFetchResult)
        assert result.status_code == 200
        assert result.content == b'{"key": "value"}'

    def test_fetch_4xx_raises(self) -> None:
        from unittest.mock import MagicMock

        from arrow_lake.ingest.connectors_http import HttpConnector

        connector = HttpConnector(timeout_seconds=5.0, max_retries=1)
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "Not Found"
        mock_response.headers = {}

        connector._client = MagicMock()
        connector._client.get.return_value = mock_response

        with pytest.raises(HttpError, match="404"):
            connector.fetch("https://example.com/missing")

    def test_fetch_429_raises_rate_limited(self) -> None:
        from unittest.mock import MagicMock

        from arrow_lake.ingest.connectors_http import HttpConnector

        connector = HttpConnector(timeout_seconds=5.0, max_retries=1)
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.text = "Too Many Requests"
        mock_response.headers = {}

        connector._client = MagicMock()
        connector._client.get.return_value = mock_response

        with pytest.raises(HttpError, match="429"):
            connector.fetch("https://example.com/data")


class TestIngestorHttpIngest:
    """Test Ingestor.ingest_http with mocked HTTP connector."""

    def test_ingest_http_single_url(self) -> None:
        from arrow_lake.ingest.ingestor import Ingestor

        mock_manager = MagicMock()
        mock_manager.dataset_exists.return_value = False
        ingestor = Ingestor(manager=mock_manager)

        mock_result = MagicMock()
        mock_result.url = "https://example.com/data.json"
        mock_result.content = b'{"col1": [1, 2]}'

        with (
            patch("arrow_lake.ingest.connectors_http.HttpConnector") as mock_connector_cls,
            patch(
                "arrow_lake.ingest.ingestor.Ingestor._read_bytes",
                return_value=pa.table({"col1": [1]}),
            ),
        ):
            mock_connector = MagicMock()
            mock_connector.fetch.return_value = mock_result
            mock_connector_cls.return_value = mock_connector

            report = ingestor.ingest_http("test_ds", ["https://example.com/data.json"])
            assert report.total_rows == 1
            assert report.total_files == 1
            assert len(report.sources) == 1
            mock_manager.create_dataset.assert_called_once()

    def test_ingest_http_multiple_urls(self) -> None:
        from arrow_lake.ingest.ingestor import Ingestor

        mock_manager = MagicMock()
        mock_manager.dataset_exists.return_value = False
        ingestor = Ingestor(manager=mock_manager)

        mock_result1 = MagicMock()
        mock_result1.url = "https://example.com/a.json"
        mock_result1.content = b'{"x": [1]}'
        mock_result2 = MagicMock()
        mock_result2.url = "https://example.com/b.json"
        mock_result2.content = b'{"x": [2]}'

        with (
            patch("arrow_lake.ingest.connectors_http.HttpConnector") as mock_connector_cls,
            patch(
                "arrow_lake.ingest.ingestor.Ingestor._read_bytes",
                side_effect=[
                    pa.table({"x": [1]}),
                    pa.table({"x": [2]}),
                ],
            ),
        ):
            mock_connector = MagicMock()
            mock_connector.fetch.side_effect = [mock_result1, mock_result2]
            mock_connector_cls.return_value = mock_connector

            report = ingestor.ingest_http(
                "test_ds",
                ["https://example.com/a.json", "https://example.com/b.json"],
            )
            assert report.total_files == 2
            assert mock_manager.create_dataset.call_count == 1
            assert mock_manager.append_dataset.call_count == 1


class TestIngestorImageIngest:
    """Test Ingestor.ingest_images with mocked image processor."""

    def test_ingest_images_single(self, tmp_path) -> None:
        from arrow_lake.ingest.ingestor import Ingestor
        from arrow_lake.ingest.media import ImageMetadata, ProcessedImage

        mock_manager = MagicMock()
        mock_manager.dataset_exists.return_value = False
        ingestor = Ingestor(manager=mock_manager)

        img_path = tmp_path / "test.png"
        img_path.write_bytes(b"\x89PNG\r\n")

        mock_result = ProcessedImage(
            original_bytes=b"orig",
            thumbnail_bytes=b"thumb",
            preview_bytes=b"prev",
            metadata=ImageMetadata(width=100, height=50),
        )

        with patch("arrow_lake.ingest.media.ImageProcessor") as mock_proc_cls:
            mock_proc = MagicMock()
            mock_proc.process.return_value = mock_result
            mock_proc_cls.return_value = mock_proc

            report = ingestor.ingest_images("img_ds", [str(img_path)])
            assert report.total_rows == 1
            assert report.total_files == 1
            mock_manager.create_dataset.assert_called_once()
            # Verify image-specific columns are stored
            call_args = mock_manager.create_dataset.call_args
            table_arg = call_args[0][1] if call_args[0] else call_args[1].get("table")
            assert "image_data" in table_arg.column_names

    def test_ingest_images_multiple(self, tmp_path) -> None:
        from arrow_lake.ingest.ingestor import Ingestor
        from arrow_lake.ingest.media import ImageMetadata, ProcessedImage

        mock_manager = MagicMock()
        mock_manager.dataset_exists.return_value = False
        ingestor = Ingestor(manager=mock_manager)

        paths = [str(tmp_path / f"img{i}.png") for i in range(3)]
        for p in paths:
            from pathlib import Path

            Path(p).write_bytes(b"\x89PNG\r\n")

        with patch("arrow_lake.ingest.media.ImageProcessor") as mock_proc_cls:
            mock_proc = MagicMock()
            mock_proc.process.return_value = ProcessedImage(
                original_bytes=b"orig",
                thumbnail_bytes=b"thumb",
                preview_bytes=b"prev",
                metadata=ImageMetadata(width=100, height=50),
            )
            mock_proc_cls.return_value = mock_proc

            report = ingestor.ingest_images("img_ds", paths)
            assert report.total_files == 3
            assert mock_manager.create_dataset.call_count == 1
            assert mock_manager.append_dataset.call_count == 2


class TestIngestorVideoIngest:
    """Test Ingestor.ingest_videos with mocked video processor."""

    def test_ingest_videos_single(self, tmp_path) -> None:
        from arrow_lake.ingest.ingestor import Ingestor
        from arrow_lake.ingest.media import ExtractedKeyframe, VideoIngestResult

        mock_manager = MagicMock()
        mock_manager.dataset_exists.return_value = False
        ingestor = Ingestor(manager=mock_manager)

        vid_path = tmp_path / "test.mp4"
        vid_path.write_bytes(b"fake video")

        mock_result = VideoIngestResult(
            keyframes=(ExtractedKeyframe(jpeg_bytes=b"frame", timestamp_ms=0),),
            duration_ms=5000,
            scene_detection_used=True,
        )

        with patch("arrow_lake.ingest.media.VideoProcessor") as mock_proc_cls:
            mock_proc = MagicMock()
            mock_proc.extract_keyframes.return_value = mock_result
            mock_proc_cls.return_value = mock_proc

            report = ingestor.ingest_videos("vid_ds", [str(vid_path)])
            assert report.total_rows == 1
            assert report.total_files == 1
            mock_manager.create_dataset.assert_called_once()
            call_args = mock_manager.create_dataset.call_args
            table_arg = call_args[0][1] if call_args[0] else call_args[1].get("table")
            assert "video_data" in table_arg.column_names
            assert "keyframe_count" in table_arg.column_names

    def test_ingest_videos_empty_keyframes(self, tmp_path) -> None:
        from arrow_lake.ingest.ingestor import Ingestor
        from arrow_lake.ingest.media import VideoIngestResult

        mock_manager = MagicMock()
        mock_manager.dataset_exists.return_value = False
        ingestor = Ingestor(manager=mock_manager)

        vid_path = tmp_path / "empty.mp4"
        vid_path.write_bytes(b"empty video")

        mock_result = VideoIngestResult(
            keyframes=(),
            duration_ms=0,
            scene_detection_used=False,
        )

        with patch("arrow_lake.ingest.media.VideoProcessor") as mock_proc_cls:
            mock_proc = MagicMock()
            mock_proc.extract_keyframes.return_value = mock_result
            mock_proc_cls.return_value = mock_proc

            report = ingestor.ingest_videos("vid_ds", [str(vid_path)])
            assert report.total_rows == 1
            # video_data should be None for empty keyframes
            call_args = mock_manager.create_dataset.call_args
            table_arg = call_args[0][1] if call_args[0] else call_args[1].get("table")
            assert table_arg.column("video_data")[0].as_py() is None


class TestIngestorMixed:
    """Test Ingestor.ingest_mixed with mocked dependencies."""

    def test_ingest_mixed_files_only(self) -> None:
        from arrow_lake.ingest.ingestor import Ingestor

        mock_manager = MagicMock()
        mock_manager.dataset_exists.return_value = False
        ingestor = Ingestor(manager=mock_manager)

        with patch.object(
            ingestor,
            "ingest",
            return_value=MagicMock(
                total_rows=5,
                total_files=2,
                sources=(MagicMock(path="a.csv"), MagicMock(path="b.csv")),
            ),
        ):
            report = ingestor.ingest_mixed("mixed_ds", {"files": ["a.csv", "b.csv"]})
            assert report.total_rows == 5
            assert report.total_files == 2

    def test_ingest_mixed_all_modalities(self) -> None:
        from arrow_lake.ingest.ingestor import Ingestor

        mock_manager = MagicMock()
        mock_manager.dataset_exists.return_value = False
        ingestor = Ingestor(manager=mock_manager)

        with (
            patch.object(
                ingestor,
                "ingest",
                return_value=MagicMock(
                    total_rows=3,
                    total_files=1,
                    sources=(MagicMock(path="data.csv"),),
                ),
            ),
            patch.object(
                ingestor,
                "ingest_http",
                return_value=MagicMock(
                    total_rows=2,
                    total_files=1,
                    sources=(MagicMock(path="http://x.com/a.json"),),
                ),
            ),
            patch.object(
                ingestor,
                "ingest_images",
                return_value=MagicMock(
                    total_rows=1,
                    total_files=1,
                    sources=(MagicMock(path="img.png"),),
                ),
            ),
            patch.object(
                ingestor,
                "ingest_videos",
                return_value=MagicMock(
                    total_rows=1,
                    total_files=1,
                    sources=(MagicMock(path="vid.mp4"),),
                ),
            ),
            patch("arrow_lake.ingest.schema.UnifiedTableManager"),
        ):
            report = ingestor.ingest_mixed(
                "mixed_ds",
                {
                    "files": ["data.csv"],
                    "urls": ["http://x.com/a.json"],
                    "images": ["img.png"],
                    "videos": ["vid.mp4"],
                },
            )
            assert report.total_rows == 7
            assert report.total_files == 4

    def test_ingest_mixed_empty_sources(self) -> None:
        from arrow_lake.ingest.ingestor import Ingestor

        mock_manager = MagicMock()
        mock_manager.dataset_exists.return_value = False
        ingestor = Ingestor(manager=mock_manager)

        with patch("arrow_lake.ingest.schema.UnifiedTableManager"):
            report = ingestor.ingest_mixed("mixed_ds", {})
            assert report.total_rows == 0
            assert report.total_files == 0
