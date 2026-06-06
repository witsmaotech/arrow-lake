"""Tests for config conflict fixes (v1.6.0 Phase 1).

Validates:
- metrics_port differs from API port by default
- JWT mode requires credentials
- Timeout fields have minimum validation
- RAGConfig.default_top_k has validation
"""

from __future__ import annotations

import pytest

from arrow_lake.config.api import ApiConfig, AuthConfig, RateLimitConfig
from arrow_lake.config._enums import AuthMode
from arrow_lake.config.document import DocumentConfig
from arrow_lake.config.infra import HttpConfig, ObservabilityConfig
from arrow_lake.config.rag import RAGConfig


class TestMetricsPortNoConflict:
    """C1: metrics_port must not default to same as API port."""

    def test_default_metrics_port_not_8000(self) -> None:
        config = ObservabilityConfig()
        assert config.metrics_port != 8000

    def test_default_metrics_port_is_8001(self) -> None:
        config = ObservabilityConfig()
        assert config.metrics_port == 8001


class TestJWTCredentials:
    """C2: JWT mode requires either secret or key pair."""

    def test_jwt_mode_with_secret_ok(self) -> None:
        config = AuthConfig(
            auth_mode=AuthMode.JWT,
            jwt_secret_key="a" * 32,  # >= 32 chars
        )
        assert config.auth_mode == AuthMode.JWT

    def test_jwt_mode_with_key_pair_ok(self) -> None:
        config = AuthConfig(
            auth_mode=AuthMode.JWT,
            jwt_public_key="pub-key-material-that-is-long-enough-for-validation",
            jwt_private_key="priv-key-material-that-is-long-enough-for-validation",
        )
        assert config.auth_mode == AuthMode.JWT

    def test_jwt_mode_without_credentials_raises(self) -> None:
        with pytest.raises(ValueError, match="JWT auth_mode requires"):
            AuthConfig(auth_mode=AuthMode.JWT)

    def test_jwt_mode_with_only_public_key_raises(self) -> None:
        with pytest.raises(ValueError, match="JWT auth_mode requires"):
            AuthConfig(
                auth_mode=AuthMode.JWT,
                jwt_public_key="pub",
            )

    def test_non_jwt_mode_no_validation(self) -> None:
        config = AuthConfig(auth_mode=AuthMode.API_KEY)
        assert config.auth_mode == AuthMode.API_KEY


class TestTimeoutValidation:
    """C3: All timeout fields enforce minimum >= 1."""

    def test_api_request_timeout_minimum(self) -> None:
        ApiConfig(request_timeout_seconds=5.0)  # OK
        with pytest.raises(ValueError, match="request_timeout_seconds"):
            ApiConfig(request_timeout_seconds=0.5)

    def test_http_timeout_minimum(self) -> None:
        HttpConfig(timeout_seconds=5.0)  # OK
        with pytest.raises(ValueError, match="timeout_seconds"):
            HttpConfig(timeout_seconds=0)

    def test_ocr_timeout_minimum(self) -> None:
        DocumentConfig(ocr_timeout_seconds=10)  # OK
        with pytest.raises(ValueError, match="ocr_timeout_seconds"):
            DocumentConfig(ocr_timeout_seconds=0)


class TestTopKValidation:
    """C4: RAGConfig.default_top_k must be >= 1."""

    def test_valid_top_k(self) -> None:
        config = RAGConfig(default_top_k=5)
        assert config.default_top_k == 5

    def test_top_k_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="default_top_k"):
            RAGConfig(default_top_k=0)

    def test_top_k_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="default_top_k"):
            RAGConfig(default_top_k=-1)


class TestRateLimitConfig:
    """RateLimitConfig has trusted_proxies field."""

    def test_default_trusted_proxies_empty(self) -> None:
        config = RateLimitConfig()
        assert config.trusted_proxies == set()

    def test_trusted_proxies_set(self) -> None:
        config = RateLimitConfig(trusted_proxies={"10.0.0.1", "10.0.0.2"})
        assert config.trusted_proxies == {"10.0.0.1", "10.0.0.2"}
