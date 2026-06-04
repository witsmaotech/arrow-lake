"""Tests for arrow_lake/ingest/connectors.py — S3Connector coverage.

Targets uncovered lines: 95 (__repr__), 112-137 (list_files, ClientError).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from arrow_lake.ingest.connectors import ConnectorResult, S3Connector


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


class TestS3ConnectorConstructor:
    def test_requires_endpoint_url(self) -> None:
        with pytest.raises(ValueError, match="endpoint_url"):
            S3Connector(bucket="my-bucket")

    def test_empty_string_endpoint_url_raises(self) -> None:
        with pytest.raises(ValueError, match="endpoint_url"):
            S3Connector(bucket="b", endpoint_url="")

    def test_accepts_valid_args(self) -> None:
        c = S3Connector(
            bucket="data",
            prefix="raw/",
            endpoint_url="http://minio:9000",
            aws_access_key_id="ak",
            aws_secret_access_key="sk",
        )
        assert c.bucket == "data"
        assert c.prefix == "raw/"
        assert c.endpoint_url == "http://minio:9000"
        assert c.aws_access_key_id == "ak"
        assert c.aws_secret_access_key == "sk"

    def test_defaults_prefix_empty(self) -> None:
        c = S3Connector(bucket="b", endpoint_url="http://s3:9000")
        assert c.prefix == ""
        assert c.aws_access_key_id is None


# ---------------------------------------------------------------------------
# __repr__
# ---------------------------------------------------------------------------


class TestS3ConnectorRepr:
    def test_repr_format(self) -> None:
        c = S3Connector(bucket="my-bucket", prefix="data/", endpoint_url="http://s3:9000")
        r = repr(c)
        assert "S3Connector" in r
        assert "'my-bucket'" in r
        assert "'data/'" in r
        assert "'http://s3:9000'" in r

    def test_repr_without_prefix(self) -> None:
        c = S3Connector(bucket="b", endpoint_url="http://s3:9000")
        r = repr(c)
        assert "'b'" in r
        assert "''" in r  # empty prefix


# ---------------------------------------------------------------------------
# list_files — boto3 is imported locally inside list_files(),
# so we must patch ``boto3.client`` at the real boto3 module.
# ---------------------------------------------------------------------------


class TestS3ConnectorListFiles:
    def _make_pages(self, keys: list[str]) -> list[dict]:
        """Build paginator pages from key names."""
        contents = [{"Key": k} for k in keys]
        return [{"Contents": contents}]

    def _setup_mocks(self) -> tuple[MagicMock, MagicMock, MagicMock]:
        mock_client = MagicMock()
        mock_paginator = MagicMock()
        mock_client.get_paginator.return_value = mock_paginator
        return mock_client, mock_paginator, MagicMock()

    @patch("boto3.client")
    def test_list_all_files(self, mock_boto3_client: MagicMock) -> None:
        mock_client, mock_paginator, _ = self._setup_mocks()
        mock_boto3_client.return_value = mock_client
        mock_paginator.paginate.return_value = self._make_pages([
            "data/a.csv",
            "data/b.csv",
            "data/c.parquet",
        ])

        conn = S3Connector(bucket="my-bucket", endpoint_url="http://s3:9000")
        result = conn.list_files()

        assert isinstance(result, ConnectorResult)
        assert result.file_count == 3
        assert result.paths == (
            "s3://my-bucket/data/a.csv",
            "s3://my-bucket/data/b.csv",
            "s3://my-bucket/data/c.parquet",
        )

    @patch("boto3.client")
    def test_list_files_with_extension_filter(self, mock_boto3_client: MagicMock) -> None:
        mock_client, mock_paginator, _ = self._setup_mocks()
        mock_boto3_client.return_value = mock_client
        mock_paginator.paginate.return_value = self._make_pages([
            "data/a.csv",
            "data/b.parquet",
            "data/c.csv",
        ])

        conn = S3Connector(bucket="my-bucket", endpoint_url="http://s3:9000")
        result = conn.list_files(extensions=[".csv"])

        assert result.file_count == 2
        assert result.paths == (
            "s3://my-bucket/data/a.csv",
            "s3://my-bucket/data/c.csv",
        )

    @patch("boto3.client")
    def test_list_files_no_matching_extensions(
        self, mock_boto3_client: MagicMock
    ) -> None:
        mock_client, mock_paginator, _ = self._setup_mocks()
        mock_boto3_client.return_value = mock_client
        mock_paginator.paginate.return_value = self._make_pages(["data/a.csv"])

        conn = S3Connector(bucket="my-bucket", endpoint_url="http://s3:9000")
        result = conn.list_files(extensions=[".parquet"])

        assert result.file_count == 0
        assert result.paths == ()

    @patch("boto3.client")
    def test_list_files_empty_bucket(self, mock_boto3_client: MagicMock) -> None:
        mock_client, mock_paginator, _ = self._setup_mocks()
        mock_boto3_client.return_value = mock_client
        mock_paginator.paginate.return_value = [{"Contents": []}]

        conn = S3Connector(bucket="my-bucket", endpoint_url="http://s3:9000")
        result = conn.list_files()

        assert result.file_count == 0

    @patch("boto3.client")
    def test_list_files_page_without_contents_key(
        self, mock_boto3_client: MagicMock
    ) -> None:
        """Page dict has no 'Contents' key — should not crash."""
        mock_client, mock_paginator, _ = self._setup_mocks()
        mock_boto3_client.return_value = mock_client
        mock_paginator.paginate.return_value = [{}]

        conn = S3Connector(bucket="my-bucket", endpoint_url="http://s3:9000")
        result = conn.list_files()

        assert result.file_count == 0

    @patch("boto3.client")
    def test_list_files_uses_credentials(self, mock_boto3_client: MagicMock) -> None:
        mock_client, mock_paginator, _ = self._setup_mocks()
        mock_boto3_client.return_value = mock_client
        mock_paginator.paginate.return_value = self._make_pages(["f.csv"])

        conn = S3Connector(
            bucket="b",
            endpoint_url="http://s3:9000",
            aws_access_key_id="key",
            aws_secret_access_key="secret",
        )
        conn.list_files()

        mock_boto3_client.assert_called_once_with(
            "s3",
            endpoint_url="http://s3:9000",
            aws_access_key_id="key",
            aws_secret_access_key="secret",
        )


# ---------------------------------------------------------------------------
# list_files — ClientError path
# ---------------------------------------------------------------------------


class TestS3ConnectorErrors:
    @patch("boto3.client")
    def test_client_error_raises_connection_error(
        self, mock_boto3_client: MagicMock
    ) -> None:
        mock_client = MagicMock()
        mock_paginator = MagicMock()
        mock_client.get_paginator.return_value = mock_paginator
        mock_boto3_client.return_value = mock_client

        error_response = {"Error": {"Code": "AccessDenied", "Message": "Forbidden"}}
        mock_paginator.paginate.side_effect = ClientError(
            error_response, "ListObjectsV2"
        )

        conn = S3Connector(
            bucket="my-bucket", prefix="data/", endpoint_url="http://s3:9000"
        )
        with pytest.raises(ConnectionError, match="Failed to list S3 objects"):
            conn.list_files()

    @patch("boto3.client")
    def test_client_error_includes_bucket_and_prefix(
        self, mock_boto3_client: MagicMock
    ) -> None:
        mock_client = MagicMock()
        mock_paginator = MagicMock()
        mock_client.get_paginator.return_value = mock_paginator
        mock_boto3_client.return_value = mock_client

        error_response = {"Error": {"Code": "NoSuchBucket", "Message": "gone"}}
        mock_paginator.paginate.side_effect = ClientError(
            error_response, "ListObjectsV2"
        )

        conn = S3Connector(
            bucket="gone-bucket", prefix="p/", endpoint_url="http://s3:9000"
        )
        with pytest.raises(ConnectionError, match="gone-bucket/p/"):
            conn.list_files()
