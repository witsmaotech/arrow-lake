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

    def test_redis_config_in_arrow_lake_config(self, monkeypatch) -> None:
        """ArrowLakeConfig must include redis with sensible defaults."""
        # 隔离本地 .env 覆盖：ArrowLakeConfig 是 pydantic-settings，env_file=".env"
        # 会读文件（monkeypatch.delenv 只清 os.environ，挡不住文件读取），故显式
        # 禁用 env_file + 清 os.environ，仅测代码层默认值。
        monkeypatch.delenv("ARROW_LAKE__REDIS__ENABLED", raising=False)
        from arrow_lake.config import ArrowLakeConfig

        config = ArrowLakeConfig(_env_file=None)
        assert config.redis.enabled is False

