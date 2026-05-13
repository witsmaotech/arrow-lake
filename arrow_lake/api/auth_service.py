"""JWT token creation and verification service.

Uses PyJWT for encoding/decoding. Gracefully no-ops when PyJWT is not installed.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections import OrderedDict
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
    ) -> None:
        self._secret_key = secret_key
        self._algorithm = algorithm
        self._public_key = public_key
        self._private_key = private_key
        self._access_minutes = access_token_minutes
        self._refresh_days = refresh_token_days
        self._issuer = issuer
        self._blacklist: OrderedDict[str, float] = OrderedDict()
        self._blacklist_lock = threading.Lock()
        self._blacklist_max_size = 100_000
        self._redis: object | None = None

        if _JWT_AVAILABLE:
            self._validate_config()

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
        )

    def create_refresh_token(
        self,
        user_id: str,
        role: Role = Role.VIEWER,
        permissions: list[str] | None = None,
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
        return self.create_access_token(
            user_id=old_payload.sub,
            role=old_payload.role,
            permissions=old_payload.permissions,
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
            )
        except jwt.ExpiredSignatureError:
            raise ValueError("Token expired") from None
        except jwt.InvalidTokenError as exc:
            raise ValueError(f"Invalid token: {exc}") from None

        # Check blacklist
        jti = data.get("jti", "")
        if jti and self.is_revoked(jti):
            raise ValueError("Token has been revoked")

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
            logger.warning("JWT secret key is empty — tokens will be insecure")

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
