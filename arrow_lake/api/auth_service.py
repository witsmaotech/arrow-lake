"""JWT token creation and verification service.

Uses PyJWT for encoding/decoding. Gracefully no-ops when PyJWT is not installed.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from arrow_lake.api.auth_models import Role, TokenPayload

logger = logging.getLogger(__name__)

_JWT_AVAILABLE = False
try:
    import jwt

    _JWT_AVAILABLE = True
except ImportError:
    jwt = None  # type: ignore[assignment]


class AuthService:
    """JWT token management service.

    Provides methods for creating access/refresh tokens, verifying tokens,
    refreshing expired access tokens, and revoking tokens.
    """

    def __init__(
        self,
        *,
        secret_key: str = "",
        algorithm: str = "HS256",
        public_key: str = "",
        private_key: str = "",
        access_token_minutes: int = 30,
        refresh_token_days: int = 7,
        issuer: str = "arrow-lake",
        audience: str = "",
        require_audience: bool = False,
    ) -> None:
        self._secret_key = secret_key
        self._algorithm = algorithm
        self._public_key = public_key
        self._private_key = private_key
        self._access_minutes = access_token_minutes
        self._refresh_days = refresh_token_days
        self._issuer = issuer
        # v1.10.5 M0: audience claim. Tokens minted here carry ``aud``;
        # enforcement is opt-in via ``require_audience`` so pre-v1.10.5
        # tokens (no aud) keep verifying during the compatibility window.
        self._audience = audience
        self._require_audience = require_audience
        self._tva_provider: Callable[[str], float | None] | None = None
        self._blacklist: OrderedDict[str, float] = OrderedDict()
        self._blacklist_lock = threading.Lock()
        self._blacklist_max_size = 100_000
        self._redis: object | None = None

        if _JWT_AVAILABLE:
            self._validate_config()

    def set_token_valid_after_provider(
        self, provider: Callable[[str], float | None] | None
    ) -> None:
        """Set a per-user token cutoff lookup (v1.10.5 M0).

        The provider maps a token ``sub`` (user id string) to an epoch-seconds
        cutoff, or None when unknown/not applicable (e.g. the shared
        ``api-user`` identity). Tokens issued (``iat``) before the cutoff are
        rejected — deactivating a user or changing their password/role takes
        effect on the next request instead of waiting out the access TTL.
        Provider errors fail open (skip the check) to match the
        system_db-disabled behaviour.
        """
        self._tva_provider = provider

    def set_redis(self, client: object) -> None:
        """Set an optional Redis client for persistent blacklist."""
        self._redis = client
        if _JWT_AVAILABLE:
            self._validate_config()

    def create_access_token(
        self,
        user_id: str,
        role: Role = Role.VIEWER,
        permissions: list[str] | None = None,
        username: str | None = None,
    ) -> TokenPayload:
        """Create a TokenPayload for an access token."""
        now = datetime.now(UTC)
        return TokenPayload(
            sub=user_id,
            role=role,
            permissions=permissions or [],
            exp=now + timedelta(minutes=self._access_minutes),
            iat=now,
            iss=self._issuer,
            jti=uuid.uuid4().hex,
            username=username,
            aud=self._audience,
        )

    def create_refresh_token(
        self,
        user_id: str,
        role: Role = Role.VIEWER,
        permissions: list[str] | None = None,
        username: str | None = None,
    ) -> str:
        """Create and encode a refresh token."""
        now = datetime.now(UTC)
        payload = TokenPayload(
            sub=user_id,
            role=role,
            permissions=permissions or [],
            exp=now + timedelta(days=self._refresh_days),
            iat=now,
            iss=self._issuer,
            jti=uuid.uuid4().hex,
            username=username,
            aud=self._audience,
        )
        return self._encode(payload)

    def revoke_token(self, jti: str) -> None:
        """Add a token's jti to the blacklist."""
        now = datetime.now(UTC).timestamp()
        ttl = self._refresh_days * 86400 + 3600
        if self._redis is not None:
            try:
                self._redis.setex(f"jwt:blacklist:{jti}", ttl, "1")
            except Exception:
                logger.warning("Redis blacklist write failed, falling back to in-memory")
        with self._blacklist_lock:
            self._blacklist[jti] = now
            # Evict oldest entries, but only those past their TTL
            cutoff = now - ttl
            while len(self._blacklist) > self._blacklist_max_size:
                oldest_jti, oldest_time = next(iter(self._blacklist))
                if oldest_time >= cutoff:
                    break  # All remaining entries are within TTL
                del self._blacklist[oldest_jti]

    def is_revoked(self, jti: str) -> bool:
        """Check if a token has been revoked."""
        if self._redis is not None:
            try:
                if self._redis.exists(f"jwt:blacklist:{jti}"):
                    return True
            except Exception:
                pass
        now = datetime.now(UTC).timestamp()
        with self._blacklist_lock:
            entry = self._blacklist.get(jti)
            if entry is None:
                return False
            cutoff = now - (self._refresh_days * 86400 + 3600)
            if entry < cutoff:
                del self._blacklist[jti]
                return False
            return True

    def refresh_access_token(self, refresh_token: str) -> TokenPayload:
        """Verify a refresh token and return a new access token payload."""
        old_payload = self.verify_token(refresh_token)
        # Revoke the old refresh token to prevent reuse (rotation)
        if old_payload.jti:
            self.revoke_token(old_payload.jti)
        return self.create_access_token(
            user_id=old_payload.sub,
            role=old_payload.role,
            permissions=old_payload.permissions,
            username=old_payload.username,
        )

    def verify_token(self, token: str, *, require_refresh: bool = False) -> TokenPayload:
        """Verify and decode a JWT token. Raises ValueError on failure.

        Args:
            token: JWT string.
            require_refresh: If True, only accept tokens with refresh-level TTL.
        """
        if not _JWT_AVAILABLE:
            raise ValueError("PyJWT not installed — cannot verify tokens")

        try:
            data = jwt.decode(
                token,
                self._verification_key(),
                algorithms=[self._algorithm],
                issuer=self._issuer,
                # v1.10.5 M0: aud enforced manually below — PyJWT (>=2.12)
                # rejects any aud-bearing token when no audience param is
                # passed, which would break the legacy-token compatibility
                # window.
                options={"verify_aud": False},
            )
        except jwt.ExpiredSignatureError:
            raise ValueError("Token expired") from None
        except jwt.InvalidTokenError as exc:
            raise ValueError(f"Invalid token: {exc}") from None

        # Check blacklist
        jti = data.get("jti", "")
        if jti and self.is_revoked(jti):
            raise ValueError("Token has been revoked")

        # v1.10.5 M0: audience enforcement (opt-in — legacy tokens without
        # aud stay valid while require_audience is False).
        if self._require_audience and data.get("aud", "") != self._audience:
            raise ValueError("Invalid audience")

        # v1.10.5 M0: per-user cutoff — reject tokens issued before the
        # user's token_valid_after (deactivate / password or role change).
        if self._tva_provider is not None:
            try:
                cutoff = self._tva_provider(str(data.get("sub", "")))
            except Exception:
                cutoff = None  # store unreachable → fail open (pre-M0 behaviour)
            if cutoff is not None:
                iat = data.get("iat", 0)
                if isinstance(iat, (int, float)) and iat < cutoff:
                    raise ValueError("Token has been revoked (issued before user cutoff)")

        if require_refresh:
            now = datetime.now(UTC)
            exp = data.get("exp", 0)
            if isinstance(exp, (int, float)):
                created = data.get("iat", now)
                ttl = exp - (created if isinstance(created, (int, float)) else int(now.timestamp()))
                if ttl < 3600:
                    raise ValueError("Expected refresh token, got access token")
            else:
                raise ValueError("Token missing expiry")

        # JWT returns exp/iat as integers, convert to datetime for Pydantic
        for key in ("exp", "iat"):
            if key in data and isinstance(data[key], (int, float)):
                data[key] = datetime.fromtimestamp(data[key], tz=UTC)

        return TokenPayload.model_validate(data)

    def _encode(self, payload: TokenPayload) -> str:
        """Encode a TokenPayload to a JWT string."""
        if not _JWT_AVAILABLE:
            raise ValueError("PyJWT not installed — cannot encode tokens")
        data = payload.model_dump(mode="json")
        # PyJWT requires exp/iat as integer timestamps, not datetime strings
        for key in ("exp", "iat"):
            if key in data and isinstance(data[key], str):
                data[key] = int(datetime.fromisoformat(data[key]).timestamp())
            elif key in data and hasattr(data[key], "timestamp"):
                data[key] = int(data[key].timestamp())
        return jwt.encode(
            data,
            self._signing_key(),
            algorithm=self._algorithm,
        )

    def _validate_config(self) -> None:
        """Validate key configuration based on algorithm."""
        algo = self._algorithm.upper()
        if algo in ("RS256", "ES256", "PS256"):
            if not self._private_key or not self._public_key:
                raise ValueError(
                    f"Algorithm '{algo}' requires both jwt_public_key and jwt_private_key"
                )
        elif algo == "HS256" and not self._secret_key:
            raise ValueError(
                "JWT secret_key is required for HS256. "
                "Set ARROW_LAKE__AUTH__JWT_SECRET_KEY environment variable."
            )

    def _signing_key(self) -> str:
        """Return the key used for encoding (signing)."""
        algo = self._algorithm.upper()
        if algo in ("RS256", "ES256", "PS256"):
            return self._private_key
        return self._secret_key

    def _verification_key(self) -> str:
        """Return the key used for decoding (verification)."""
        algo = self._algorithm.upper()
        if algo in ("RS256", "ES256", "PS256"):
            return self._public_key
        return self._secret_key
