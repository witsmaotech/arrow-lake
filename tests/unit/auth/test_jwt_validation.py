"""Tests for JWT secret key validation (Round 4 — H1 fix)."""

import pytest

from arrow_lake.config.api import AuthConfig


class TestJwtSecretValidation:
    """Verify jwt_secret_key rejects weak secrets."""

    def test_empty_string_allowed(self):
        cfg = AuthConfig(jwt_secret_key="")
        assert cfg.jwt_secret_key == ""

    def test_strong_secret_accepted(self):
        secret = "a" * 32
        cfg = AuthConfig(jwt_secret_key=secret)
        assert cfg.jwt_secret_key == secret

    def test_exactly_32_chars_accepted(self):
        secret = "0123456789abcdef0123456789abcdef"
        cfg = AuthConfig(jwt_secret_key=secret)
        assert len(cfg.jwt_secret_key) == 32

    def test_31_chars_rejected(self):
        secret = "a" * 31
        with pytest.raises(ValueError, match=">= 32 characters"):
            AuthConfig(jwt_secret_key=secret)

    def test_16_chars_rejected(self):
        secret = "short_secret_key"
        with pytest.raises(ValueError, match=">= 32 characters"):
            AuthConfig(jwt_secret_key=secret)

    def test_1_char_rejected(self):
        with pytest.raises(ValueError, match=">= 32 characters"):
            AuthConfig(jwt_secret_key="x")

    def test_long_secret_accepted(self):
        secret = "a" * 256
        cfg = AuthConfig(jwt_secret_key=secret)
        assert len(cfg.jwt_secret_key) == 256
