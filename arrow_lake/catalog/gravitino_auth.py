"""Gravitino authentication providers — inject auth headers into REST calls.

Supported auth types:
- Simple: X-Gravitino-Authorization header with Base64-encoded user info
- OAuth2: Bearer token from a configurable token endpoint
- Kerberos: Negotiate SPNEGO token (requires system Kerberos setup)
- None: No auth headers (backward-compatible default)
"""

from __future__ import annotations

import base64
import threading
import time
from typing import Any

import structlog

from arrow_lake.config.gravitino import GravitinoAuthType

logger = structlog.get_logger(__name__)


class GravitinoAuthProvider:
    """Base class for Gravitino authentication providers."""

    def auth_headers(self) -> dict[str, str]:
        """Return auth headers to add to every Gravitino REST request."""
        return {}

    def authenticate(self, request: Any) -> Any:
        """Add auth headers to a urllib Request object."""
        for key, value in self.auth_headers().items():
            request.add_header(key, value)
        return request


class SimpleAuthProvider(GravitinoAuthProvider):
    """Simple auth: user identity passed to Gravitino SDK only.

    The Gravitino REST server with simple authenticator and
    authorization_enable=false does NOT accept Authorization: Simple
    header (401). The user identity is used by the Python SDK
    internally; for direct REST proxy calls we send no auth header.
    """

    def __init__(self, user: str = "arrow_lake") -> None:
        self._user = user

    @property
    def user(self) -> str:
        return self._user

    def auth_headers(self) -> dict[str, str]:
        return {}


class OAuth2AuthProvider(GravitinoAuthProvider):
    """OAuth2 auth: Bearer token from a token endpoint."""

    def __init__(
        self,
        token_url: str,
        client_id: str,
        client_secret: str,
        *,
        token_audience: str = "gravitino",
    ) -> None:
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._token_audience = token_audience
        self._token: str = ""
        self._expires_at: float = 0
        self._lock = threading.Lock()

    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._get_token()}"}

    def _get_token(self) -> str:
        with self._lock:
            if self._token and time.time() < self._expires_at - 30:
                return self._token
            self._refresh_token()
            return self._token

    def _refresh_token(self) -> None:
        import json
        from urllib.parse import urlencode
        from urllib.request import Request, urlopen

        # Enforce HTTPS to prevent credential leakage over plaintext
        if not self._token_url.startswith("https://"):
            raise ValueError(
                f"OAuth2 token_url must use HTTPS, got: {self._token_url.split('://')[0]}://"
            )

        body = urlencode({
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "audience": self._token_audience,
        }).encode()
        req = Request(self._token_url, data=body, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            self._token = data["access_token"]
            self._expires_at = time.time() + data.get("expires_in", 3600)
            logger.debug("gravitino_oauth2_token_refreshed")
        except Exception:
            logger.error("gravitino_oauth2_token_failed: token_url=%s", self._token_url)
            raise


class KerberosAuthProvider(GravitinoAuthProvider):
    """Kerberos auth: Negotiate SPNEGO token."""

    def __init__(self, principal: str, keytab: str) -> None:
        self._principal = principal
        self._keytab = keytab

    def auth_headers(self) -> dict[str, str]:
        token = self._get_spnego_token()
        return {"Authorization": f"Negotiate {token}"}

    def _get_spnego_token(self) -> str:
        try:
            import subprocess

            result = subprocess.run(
                ["kinit", "-k", "-t", self._keytab, self._principal],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                logger.error("gravitino_kerberos_kinit_failed", error=result.stderr)
                raise RuntimeError(f"kinit failed: {result.stderr}")

            import base64

            import gssapi

            server_name = gssapi.Name(self._principal)
            ctx = server_name.initiate_context()
            token = ctx.step()
            if not token:
                raise RuntimeError("SPNEGO token generation returned empty token")
            return base64.b64encode(token).decode()
        except Exception as exc:
            logger.error("gravitino_kerberos_auth_failed", error=str(exc))
            raise


class NullAuthProvider(GravitinoAuthProvider):
    """No auth — used when auth is not configured. Logs a warning once."""

    _warned = False

    def auth_headers(self) -> dict[str, str]:
        if not NullAuthProvider._warned:
            logger.warning(
                "gravitino_auth_not_configured",
                msg="Gravitino REST calls are unauthenticated. "
                "Set auth_type in config to enable authentication.",
            )
            NullAuthProvider._warned = True
        return {}


def create_auth_provider(config: Any) -> GravitinoAuthProvider:
    """Create an auth provider from Gravitino config.

    Args:
        config: GravitinoConfig instance.

    Returns:
        Appropriate GravitinoAuthProvider based on config.auth_type.
        Returns NullAuthProvider when Gravitino is disabled.
    """
    if not getattr(config, "enabled", False):
        return NullAuthProvider()

    auth_type = getattr(config, "auth_type", None)

    if auth_type == GravitinoAuthType.OAUTH:
        return OAuth2AuthProvider(
            token_url=getattr(config, "auth_oauth2_token_url", ""),
            client_id=getattr(config, "auth_oauth2_client_id", ""),
            client_secret=getattr(config, "auth_oauth2_client_secret", ""),
        )

    if auth_type == GravitinoAuthType.KERBEROS:
        return KerberosAuthProvider(
            principal=getattr(config, "auth_kerberos_principal", ""),
            keytab=getattr(config, "auth_kerberos_keytab", ""),
        )

    # Default: Simple auth (also the GravitinoAuthType.SIMPLE path)
    user = getattr(config, "auth_simple_user", "arrow_lake")
    return SimpleAuthProvider(user=user)
