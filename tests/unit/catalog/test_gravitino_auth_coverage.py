"""Comprehensive tests for catalog/gravitino_auth.py — targeting uncovered paths.

Focus on:
- OAuth2AuthProvider: HTTPS enforcement, token refresh, thread safety
- KerberosAuthProvider: kinit failure, SPNEGO generation
- NullAuthProvider: class-level warning flag reset
- create_auth_provider: default auth_type path (no SIMPLE match)
- authenticate: base class method
"""

from __future__ import annotations

import base64
import json
import threading
from unittest.mock import MagicMock, patch

import pytest

from arrow_lake.catalog.gravitino_auth import (
    GravitinoAuthProvider,
    KerberosAuthProvider,
    NullAuthProvider,
    OAuth2AuthProvider,
    SimpleAuthProvider,
    create_auth_provider,
)
from arrow_lake.config.gravitino import GravitinoAuthType


# ---------------------------------------------------------------------------
# OAuth2AuthProvider — additional coverage
# ---------------------------------------------------------------------------


class TestOAuth2AuthProviderExtra:
    """Extended OAuth2 auth provider tests."""

    def test_https_enforcement_rejects_http(self) -> None:
        with pytest.raises(ValueError, match="must use HTTPS"):
            provider = OAuth2AuthProvider(
                token_url="http://insecure.example.com/token",
                client_id="cid",
                client_secret="csecret",
            )
            provider.auth_headers()

    def test_token_refresh_sends_correct_body(self) -> None:
        captured_body = {}

        def mock_urlopen(req, timeout=10):
            captured_body["data"] = req.data.decode()
            captured_body["content_type"] = req.get_header("Content-type")
            mock_resp = MagicMock()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_resp.read.return_value = json.dumps({
                "access_token": "new_token",
                "expires_in": 7200,
            }).encode()
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            provider = OAuth2AuthProvider(
                token_url="https://auth.example.com/token",
                client_id="my_client",
                client_secret="my_secret",
                token_audience="my_audience",
            )
            headers = provider.auth_headers()

        assert headers["Authorization"] == "Bearer new_token"
        assert "grant_type=client_credentials" in captured_body["data"]
        assert "client_id=my_client" in captured_body["data"]
        assert "client_secret=my_secret" in captured_body["data"]
        assert "audience=my_audience" in captured_body["data"]

    def test_token_expires_and_refreshes(self) -> None:
        call_count = 0

        def mock_urlopen(req, timeout=10):
            nonlocal call_count
            call_count += 1
            mock_resp = MagicMock()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_resp.read.return_value = json.dumps({
                "access_token": f"token_{call_count}",
                "expires_in": 1,  # 1 second, will expire quickly
            }).encode()
            return mock_resp

        import time

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            provider = OAuth2AuthProvider(
                token_url="https://auth.example.com/token",
                client_id="cid",
                client_secret="csecret",
            )
            # First call
            headers1 = provider.auth_headers()
            assert "token_1" in headers1["Authorization"]

            # Manually expire the token
            provider._expires_at = time.time() - 1

            # Second call should trigger refresh
            headers2 = provider.auth_headers()
            assert "token_2" in headers2["Authorization"]

        assert call_count == 2

    def test_token_cached_when_not_expired(self) -> None:
        call_count = 0

        def mock_urlopen(req, timeout=10):
            nonlocal call_count
            call_count += 1
            mock_resp = MagicMock()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_resp.read.return_value = json.dumps({
                "access_token": "cached_token",
                "expires_in": 3600,
            }).encode()
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            provider = OAuth2AuthProvider(
                token_url="https://auth.example.com/token",
                client_id="cid",
                client_secret="csecret",
            )
            provider.auth_headers()
            provider.auth_headers()

        assert call_count == 1

    def test_concurrent_token_refresh_thread_safety(self) -> None:
        """Multiple threads requesting tokens simultaneously should be safe."""
        call_count = 0
        lock = threading.Lock()

        def mock_urlopen(req, timeout=10):
            nonlocal call_count
            with lock:
                call_count += 1
            import time
            time.sleep(0.01)  # Simulate network delay
            mock_resp = MagicMock()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_resp.read.return_value = json.dumps({
                "access_token": "thread_safe_token",
                "expires_in": 3600,
            }).encode()
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            provider = OAuth2AuthProvider(
                token_url="https://auth.example.com/token",
                client_id="cid",
                client_secret="csecret",
            )

            results = []

            def get_headers():
                results.append(provider.auth_headers())

            threads = [threading.Thread(target=get_headers) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            # All should get the token
            assert len(results) == 5
            assert all(r["Authorization"] == "Bearer thread_safe_token" for r in results)

    def test_default_expires_in(self) -> None:
        """When expires_in not in response, default to 3600."""
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_resp.read.return_value = json.dumps({
                "access_token": "tok",
            }).encode()
            mock_urlopen.return_value = mock_resp

            provider = OAuth2AuthProvider(
                token_url="https://auth.example.com/token",
                client_id="cid",
                client_secret="csecret",
            )
            provider.auth_headers()

            # expires_in defaults to 3600
            assert provider._expires_at > 0


# ---------------------------------------------------------------------------
# KerberosAuthProvider
# ---------------------------------------------------------------------------


class TestKerberosAuthProviderExtra:
    """Extended Kerberos auth provider tests."""

    @patch("subprocess.run")
    def test_kinit_failure_raises_runtime_error(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1, stderr="kinit: Bad format")

        provider = KerberosAuthProvider(
            principal="HTTP/gravitino@REALM",
            keytab="/etc/krb5.keytab",
        )
        with pytest.raises(RuntimeError, match="kinit failed"):
            provider._get_spnego_token()

    @patch("subprocess.run")
    def test_kinit_success_but_no_spnego_token(self, mock_run: MagicMock) -> None:
        """When SPNEGO token is empty after kinit, should raise RuntimeError."""
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        with patch.dict("sys.modules", {"gssapi": MagicMock()}) as mods:
            mock_gssapi = mods["gssapi"]
            mock_name = MagicMock()
            mock_gssapi.Name.return_value = mock_name
            mock_ctx = MagicMock()
            mock_name.initiate_context.return_value = mock_ctx
            mock_ctx.step.return_value = None  # Empty token

            provider = KerberosAuthProvider(
                principal="HTTP/gravitino@REALM",
                keytab="/etc/krb5.keytab",
            )
            with pytest.raises(RuntimeError, match="empty token"):
                provider._get_spnego_token()

    @patch("subprocess.run")
    def test_kinit_success_with_valid_spnego(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        with patch.dict("sys.modules", {"gssapi": MagicMock()}) as mods:
            mock_gssapi = mods["gssapi"]
            mock_name = MagicMock()
            mock_gssapi.Name.return_value = mock_name
            mock_ctx = MagicMock()
            mock_name.initiate_context.return_value = mock_ctx
            mock_ctx.step.return_value = b"valid_spnego_bytes"

            provider = KerberosAuthProvider(
                principal="HTTP/gravitino@REALM",
                keytab="/etc/krb5.keytab",
            )
            token = provider._get_spnego_token()
            assert isinstance(token, str)
            # base64.b64encode(b"valid_spnego_bytes").decode() is deterministic
            import base64 as b64mod
            assert token == b64mod.b64encode(b"valid_spnego_bytes").decode()

    def test_auth_headers_calls_get_spnego(self) -> None:
        provider = KerberosAuthProvider(
            principal="HTTP/gravitino@REALM",
            keytab="/etc/krb5.keytab",
        )
        with patch.object(provider, "_get_spnego_token", return_value="spnego_tok"):
            headers = provider.auth_headers()
            assert headers["Authorization"] == "Negotiate spnego_tok"


# ---------------------------------------------------------------------------
# NullAuthProvider — class-level state
# ---------------------------------------------------------------------------


class TestNullAuthProviderState:
    """Test NullAuthProvider class-level warning behavior."""

    def setup_method(self) -> None:
        NullAuthProvider._warned = False

    def test_first_call_sets_warned(self) -> None:
        provider = NullAuthProvider()
        assert NullAuthProvider._warned is False
        provider.auth_headers()
        assert NullAuthProvider._warned is True

    def test_second_call_does_not_warn_again(self) -> None:
        provider = NullAuthProvider()
        provider.auth_headers()
        assert NullAuthProvider._warned is True

        # Reset a spy to verify no second warning
        provider2 = NullAuthProvider()
        headers = provider2.auth_headers()
        assert headers == {}
        assert NullAuthProvider._warned is True  # Still True from first call

    def test_authenticate_returns_unchanged_request(self) -> None:
        provider = NullAuthProvider()
        req = MagicMock()
        result = provider.authenticate(req)
        assert result is req
        req.add_header.assert_not_called()


# ---------------------------------------------------------------------------
# GravitinoAuthProvider base class
# ---------------------------------------------------------------------------


class TestGravitinoAuthProviderBase:
    """Test base class methods."""

    def test_base_auth_headers_empty(self) -> None:
        provider = GravitinoAuthProvider()
        assert provider.auth_headers() == {}

    def test_base_authenticate_returns_request(self) -> None:
        provider = GravitinoAuthProvider()
        req = MagicMock()
        result = provider.authenticate(req)
        assert result is req
        # No headers added since auth_headers() returns empty dict
        req.add_header.assert_not_called()


# ---------------------------------------------------------------------------
# create_auth_provider — additional paths
# ---------------------------------------------------------------------------


class TestCreateAuthProviderExtra:
    """Additional factory function tests."""

    def setup_method(self) -> None:
        NullAuthProvider._warned = False

    def test_simple_auth_type_explicit(self) -> None:
        config = MagicMock()
        config.enabled = True
        config.auth_type = GravitinoAuthType.SIMPLE
        config.auth_simple_user = "explicit_user"
        provider = create_auth_provider(config)
        assert isinstance(provider, SimpleAuthProvider)
        # Simple auth deliberately returns empty headers (no Authorization)
        assert provider.auth_headers() == {}

    def test_default_user_from_config(self) -> None:
        config = MagicMock()
        config.enabled = True
        config.auth_type = GravitinoAuthType.SIMPLE
        config.auth_simple_user = "default_arrow_lake"
        provider = create_auth_provider(config)
        assert isinstance(provider, SimpleAuthProvider)
        assert provider.auth_headers() == {}

    def test_authenticate_propagates_to_simple(self) -> None:
        config = MagicMock()
        config.enabled = True
        config.auth_type = GravitinoAuthType.SIMPLE
        config.auth_simple_user = "admin"
        provider = create_auth_provider(config)

        req = MagicMock()
        result = provider.authenticate(req)
        assert result is req
        # Simple auth must NOT add any header to the request
        req.add_header.assert_not_called()
