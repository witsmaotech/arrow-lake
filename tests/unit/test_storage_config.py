"""Tests for StorageConfig enhancement with S3 helper methods.

M0a Day 1 — TDD RED phase.
"""

from __future__ import annotations

import os
from unittest.mock import patch

from arrow_lake.config import StorageBackend, StorageConfig


class TestToStorageOptions:
    """Test to_storage_options() method."""

    def test_returns_none_for_local_backend(self) -> None:
        """Local backend should return None (no storage_options needed)."""
        config = StorageConfig(backend=StorageBackend.LOCAL)
        assert config.to_storage_options() is None

    def test_returns_dict_for_minio_backend(self) -> None:
        """MinIO backend should return dict with S3 keys."""
        config = StorageConfig(
            backend=StorageBackend.MINIO,
            s3_endpoint="http://localhost:9000",
            s3_region="us-east-1",
            s3_access_key="minioadmin",
            s3_secret_key="minioadmin",
        )
        opts = config.to_storage_options()
        assert opts is not None
        assert isinstance(opts, dict)
        assert opts["region"] == "us-east-1"
        assert opts["endpoint_url"] == "http://localhost:9000"
        assert opts["aws_access_key_id"] == "minioadmin"
        assert opts["aws_secret_access_key"] == "minioadmin"
        assert opts["allow_anonymous"] == "false"

    def test_dict_has_required_keys(self) -> None:
        """Returned dict must contain all required keys for lance/boto3."""
        config = StorageConfig(backend=StorageBackend.MINIO)
        opts = config.to_storage_options()
        required = {
            "region",
            "endpoint_url",
            "aws_access_key_id",
            "aws_secret_access_key",
            "allow_anonymous",
        }
        for key in required:
            assert key in opts, f"Missing required key: {key}"


class TestToDuckdbS3Config:
    """Test to_duckdb_s3_config() method."""

    def test_returns_empty_list_for_local(self) -> None:
        """Local backend should return empty list (no S3 SET needed)."""
        config = StorageConfig(backend=StorageBackend.LOCAL)
        assert config.to_duckdb_s3_config() == []

    def test_returns_set_statements_for_minio(self) -> None:
        """MinIO backend should return list of SET statements."""
        config = StorageConfig(
            backend=StorageBackend.MINIO,
            s3_endpoint="http://localhost:9000",
            s3_region="us-east-1",
            s3_access_key="key",
            s3_secret_key="secret",
        )
        statements = config.to_duckdb_s3_config()
        assert len(statements) == 4
        assert any("s3_region" in s for s in statements)
        assert any("s3_endpoint" in s for s in statements)
        assert any("s3_access_key_id" in s for s in statements)
        assert any("s3_secret_access_key" in s for s in statements)

    def test_statements_are_valid_sql(self) -> None:
        """Each statement should be a valid DuckDB SET statement."""
        config = StorageConfig(backend=StorageBackend.MINIO)
        for stmt in config.to_duckdb_s3_config():
            assert stmt.startswith("SET ")
            assert "=" in stmt

    def test_escapes_single_quotes_in_credentials(self) -> None:
        """Single quotes in credentials should be escaped (SQL injection prevention)."""
        config = StorageConfig(
            backend=StorageBackend.MINIO,
            s3_access_key="key'with'quotes",
            s3_secret_key="secret'evil",
        )
        statements = config.to_duckdb_s3_config()
        access_stmt = next(s for s in statements if "s3_access_key_id" in s)
        secret_stmt = next(s for s in statements if "s3_secret_access_key" in s)
        assert "key''with''quotes" in access_stmt
        assert "secret''evil" in secret_stmt


class TestS3Uri:
    """Test s3_uri property."""

    def test_returns_base_uri_for_local(self) -> None:
        config = StorageConfig(backend=StorageBackend.LOCAL, base_uri="./data")
        assert config.s3_uri == "./data"

    def test_returns_s3_uri_for_minio(self) -> None:
        config = StorageConfig(backend=StorageBackend.MINIO, s3_bucket="my-bucket")
        assert config.s3_uri == "s3://my-bucket/data"


class TestFromEnv:
    """Test from_env() classmethod."""

    def test_reads_from_environment_variables(self) -> None:
        """Should read S3_*/AWS_ prefixed environment variables."""
        env = {
            "S3_ENDPOINT": "http://minio:9000",
            "AWS_ACCESS_KEY_ID": "env-key",
            "AWS_SECRET_ACCESS_KEY": "env-secret",
            "S3_BUCKET": "env-bucket",
            "AWS_REGION": "eu-west-1",
        }
        with patch.dict(os.environ, env, clear=True):
            config = StorageConfig.from_env()
            assert config.s3_endpoint == "http://minio:9000"
            assert config.s3_access_key == "env-key"
            assert config.s3_secret_key == "env-secret"
            assert config.s3_bucket == "env-bucket"
            assert config.s3_region == "eu-west-1"

    def test_defaults_when_env_not_set(self) -> None:
        """Should use field defaults when env vars are not set."""
        with patch.dict(os.environ, {}, clear=True):
            config = StorageConfig.from_env()
            assert config.backend == StorageBackend.MINIO
            assert config.s3_bucket == "arrow-lake"
            assert config.s3_region == "us-east-1"
