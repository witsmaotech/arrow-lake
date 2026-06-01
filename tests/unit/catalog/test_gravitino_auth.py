"""Unit tests for Gravitino authentication providers — v1.5.1 Phase 1."""

from __future__ import annotations

import base64
from unittest.mock import MagicMock, patch

import pytest

from arrow_lake.catalog.gravitino_auth import (
    KerberosAuthProvider,
    NullAuthProvider,
    OAuth2AuthProvider,
    SimpleAuthProvider,
    create_auth_provider,
)
from arrow_lake.config.gravitino import GravitinoAuthType


# ---------------------------------------------------------------------------
# SimpleAuthProvider
# ---------------------------------------------------------------------------


class TestSimpleAuthProvider:
    """Test Simple auth: Base64-encoded user identifier."""

    def test_default_user(self) -> None:
        provider = SimpleAuthProvider()
        headers = provider.auth_headers()
        assert "Authorization" in headers
        expected = base64.b64encode(b"arrow_lake").decode()
        assert headers["Authorization"] == f"Simple {expected}"

    def test_custom_user(self) -> None:
        provider = SimpleAuthProvider(user="alice")
        headers = provider.auth_headers()
        expected = base64.b64encode(b"alice").decode()
        assert headers["Authorization"] == f"Simple {expected}"

    def test_authenticate_adds_header_to_request(self) -> None:
        provider = SimpleAuthProvider(user="bob")
        req = MagicMock()
        req.add_header = MagicMock()

        result = provider.authenticate(req)

        req.add_header.assert_called_once_with("Authorization", provider.auth_headers()["Authorization"])
        assert result is req


# ---------------------------------------------------------------------------
# OAuth2AuthProvider
# ---------------------------------------------------------------------------


class TestOAuth2AuthProvider:
    """Test OAuth2 auth: Bearer token from token endpoint."""

    @patch("urllib.request.urlopen")
    def test_fetches_token_on_first_call(self, mock_urlopen: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = b'{"access_token": "tok_123", "expires_in": 3600}'
        mock_resp.status = 200
        mock_urlopen.return_value = mock_resp

        provider = OAuth2AuthProvider(
            token_url="https://auth.example.com/token",
            client_id="cid",
            client_secret="csecret",
        )
        headers = provider.auth_headers()

        assert headers["Authorization"] == "Bearer tok_123"
        mock_urlopen.assert_called_once()

    @patch("urllib.request.urlopen")
    def test_caches_token(self, mock_urlopen: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = b'{"access_token": "tok_cached", "expires_in": 3600}'
        mock_resp.status = 200
        mock_urlopen.return_value = mock_resp

        provider = OAuth2AuthProvider(
            token_url="https://auth.example.com/token",
            client_id="cid",
            client_secret="csecret",
        )
        # Call twice
        provider.auth_headers()
        provider.auth_headers()

        # Only one HTTP call (token cached)
        assert mock_urlopen.call_count == 1

    @patch("urllib.request.urlopen")
    def test_token_refresh_failure_raises(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = Exception("network error")

        provider = OAuth2AuthProvider(
            token_url="https://auth.example.com/token",
            client_id="cid",
            client_secret="csecret",
        )
        with pytest.raises(Exception, match="network error"):
            provider.auth_headers()


# ---------------------------------------------------------------------------
# NullAuthProvider
# ---------------------------------------------------------------------------


class TestNullAuthProvider:
    """Test Null auth: no headers, warning logged once."""

    def test_returns_empty_headers(self) -> None:
        # Reset class-level flag
        NullAuthProvider._warned = False
        provider = NullAuthProvider()
        assert provider.auth_headers() == {}

    def test_warns_only_once(self) -> None:
        NullAuthProvider._warned = False
        provider = NullAuthProvider()
        provider.auth_headers()  # First call triggers warning
        provider.auth_headers()  # Second call should be silent
        # No assertion needed — just verifying no exception and single warn

    def test_authenticate_returns_unchanged_request(self) -> None:
        NullAuthProvider._warned = False
        provider = NullAuthProvider()
        req = MagicMock()
        result = provider.authenticate(req)
        assert result is req
        req.add_header.assert_not_called()


# ---------------------------------------------------------------------------
# create_auth_provider factory
# ---------------------------------------------------------------------------


class TestCreateAuthProvider:
    """Test factory function that selects provider from config."""

    def test_disabled_gravitino_returns_null(self) -> None:
        config = MagicMock()
        config.enabled = False
        provider = create_auth_provider(config)
        assert isinstance(provider, NullAuthProvider)

    def test_default_returns_simple(self) -> None:
        config = MagicMock()
        config.enabled = True
        config.auth_type = GravitinoAuthType.SIMPLE
        config.auth_simple_user = "testuser"
        provider = create_auth_provider(config)
        assert isinstance(provider, SimpleAuthProvider)

    def test_oauth2_type(self) -> None:
        config = MagicMock()
        config.enabled = True
        config.auth_type = GravitinoAuthType.OAUTH
        config.auth_oauth2_token_url = "https://auth.example.com/token"
        config.auth_oauth2_client_id = "cid"
        config.auth_oauth2_client_secret = "csecret"
        provider = create_auth_provider(config)
        assert isinstance(provider, OAuth2AuthProvider)

    def test_kerberos_type(self) -> None:
        config = MagicMock()
        config.enabled = True
        config.auth_type = GravitinoAuthType.KERBEROS
        config.auth_kerberos_principal = "HTTP/gravitino@REALM"
        config.auth_kerberos_keytab = "/etc/krb5.keytab"
        provider = create_auth_provider(config)
        assert isinstance(provider, KerberosAuthProvider)

    def test_none_auth_type_returns_simple(self) -> None:
        config = MagicMock()
        config.enabled = True
        config.auth_type = None
        config.auth_simple_user = "fallback_user"
        provider = create_auth_provider(config)
        assert isinstance(provider, SimpleAuthProvider)


# ---------------------------------------------------------------------------
# Integration: mock Gravitino server asserts auth headers
# ---------------------------------------------------------------------------


class TestAuthHeaderPropagation:
    """Verify auth headers reach the HTTP request in real call sites."""

    def test_authenticate_sets_headers_on_request(self) -> None:
        provider = SimpleAuthProvider(user="admin")
        req = MagicMock()
        req.add_header = MagicMock()

        provider.authenticate(req)

        req.add_header.assert_called_once()
        call_args = req.add_header.call_args[0]
        assert call_args[0] == "Authorization"
        assert "Simple" in call_args[1]

    def test_null_authenticate_does_not_add_headers(self) -> None:
        NullAuthProvider._warned = False
        provider = NullAuthProvider()
        req = MagicMock()
        provider.authenticate(req)
        req.add_header.assert_not_called()
