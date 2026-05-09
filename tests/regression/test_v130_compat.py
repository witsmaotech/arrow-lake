"""Regression test — v1.3.0 version sync and sdk re-export."""

from __future__ import annotations

import tomllib
from pathlib import Path


class TestV130Compat:
    """Verify v1.3.0 changes don't break backward compatibility."""

    def test_version_sync(self) -> None:
        """_version.py and pyproject.toml must report the same version."""
        from arrow_lake._version import __version__

        pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)
        pyproject_version = data["project"]["version"]

        assert __version__ == pyproject_version, (
            f"Version mismatch: _version.py={__version__}, pyproject.toml={pyproject_version}"
        )

    def test_sdk_reexport(self) -> None:
        """arrow_lake.sdk.LakeClient must resolve to arrow_lake.Lake."""
        from arrow_lake import Lake
        from arrow_lake.sdk import LakeClient

        assert LakeClient is Lake

    def test_redis_config_defaults(self) -> None:
        """RedisConfig must be constructible with no arguments (backward compat)."""
        from arrow_lake.config import RedisConfig

        cfg = RedisConfig()
        assert cfg.enabled is False
        assert cfg.url == "redis://localhost:6379/0"
        assert cfg.password == ""
        assert cfg.ssl is False

    def test_redis_config_in_arrow_lake_config(self) -> None:
        """ArrowLakeConfig must include redis with sensible defaults."""
        from arrow_lake.config import ArrowLakeConfig

        config = ArrowLakeConfig()
        assert config.redis.enabled is False

