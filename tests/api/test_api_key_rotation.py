"""Tests for API key rotation configuration."""

from __future__ import annotations

from arrow_lake.config import ApiConfig, ArrowLakeConfig


def test_api_key_rotation_days_default() -> None:
    cfg = ApiConfig()
    assert cfg.api_key_rotation_days == 90


def test_api_key_rotation_days_custom() -> None:
    cfg = ApiConfig(api_key_rotation_days=30)
    assert cfg.api_key_rotation_days == 30


def test_api_key_rotation_in_full_config() -> None:
    cfg = ArrowLakeConfig()
    assert cfg.api.api_key_rotation_days == 90


def test_api_key_rotation_from_env() -> None:
    import os
    os.environ["ARROW_LAKE__API__API_KEY_ROTATION_DAYS"] = "60"
    try:
        cfg = ArrowLakeConfig()
        assert cfg.api.api_key_rotation_days == 60
    finally:
        del os.environ["ARROW_LAKE__API__API_KEY_ROTATION_DAYS"]
