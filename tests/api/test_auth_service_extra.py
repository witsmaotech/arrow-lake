"""Extra tests for AuthService — Redis blacklist, expiry cleanup, refresh, require_refresh, config validation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("jwt")

from arrow_lake.api.auth_models import Role
from arrow_lake.api.auth_service import AuthService

SECRET = "test-secret-key-min-32-chars-for-hmac!"


def _make_svc(**overrides) -> AuthService:
    """Create an AuthService with sensible test defaults."""
    defaults = dict(
        secret_key=SECRET,
        algorithm="HS256",
        access_token_minutes=30,
        refresh_token_days=7,
    )
    defaults.update(overrides)
    return AuthService(**defaults)


# ===========================================================================
# revoke_token — Redis path
# ===========================================================================


class TestRevokeTokenRedis:
    """Tests for revoke_token with Redis client wired."""

    def test_revoke_calls_redis_setex(self) -> None:
        """revoke_token should call redis.setex when redis is configured."""
        mock_redis = MagicMock()
        svc = _make_svc()
        svc.set_redis(mock_redis)

        svc.revoke_token("jti-abc123")

        mock_redis.setex.assert_called_once()
        call_args = mock_redis.setex.call_args
        assert call_args[0][0] == "jwt:blacklist:jti-abc123"
        assert call_args[0][2] == "1"

    def test_revoke_falls_back_on_redis_error(self) -> None:
        """revoke_token should fall back to in-memory on Redis error."""
        mock_redis = MagicMock()
        mock_redis.setex.side_effect = ConnectionError("redis down")
        svc = _make_svc()
        svc.set_redis(mock_redis)

        # Should not raise.
        svc.revoke_token("jti-fallback")

        # In-memory blacklist should still have the entry.
        assert "jti-fallback" in svc._blacklist

    def test_revoke_without_redis_uses_in_memory(self) -> None:
        """revoke_token without Redis should use in-memory blacklist only."""
        svc = _make_svc()
        svc.revoke_token("jti-mem")
        assert "jti-mem" in svc._blacklist


# ===========================================================================
# is_revoked — expiry cleanup
# ===========================================================================


class TestIsRevokedExpiry:
    """Tests for is_revoked with expired entry cleanup."""

    def test_expired_entry_cleaned_up(self) -> None:
        """Old blacklist entries beyond TTL should be removed and return False."""
        svc = _make_svc(refresh_token_days=7)
        # Manually insert an old entry (beyond the cleanup cutoff).
        old_ts = datetime.now(UTC).timestamp() - (7 * 86400 + 7200)  # well past TTL
        svc._blacklist["jti-old"] = old_ts

        assert svc.is_revoked("jti-old") is False
        # Entry should have been cleaned up.
        assert "jti-old" not in svc._blacklist

    def test_recent_entry_is_revoked(self) -> None:
        """Recent blacklist entries should return True."""
        svc = _make_svc()
        now = datetime.now(UTC).timestamp()
        svc._blacklist["jti-recent"] = now

        assert svc.is_revoked("jti-recent") is True

    def test_unknown_jti_not_revoked(self) -> None:
        """Unknown JTI should return False."""
        svc = _make_svc()
        assert svc.is_revoked("jti-unknown") is False

    def test_is_revoked_with_redis_hit(self) -> None:
        """is_revoked should return True when Redis reports the key exists."""
        mock_redis = MagicMock()
        mock_redis.exists.return_value = True
        svc = _make_svc()
        svc.set_redis(mock_redis)

        assert svc.is_revoked("jti-redis") is True
        mock_redis.exists.assert_called_once_with("jwt:blacklist:jti-redis")

    def test_is_revoked_with_redis_miss(self) -> None:
        """is_revoked should check in-memory when Redis reports key absent."""
        mock_redis = MagicMock()
        mock_redis.exists.return_value = False
        svc = _make_svc()
        svc.set_redis(mock_redis)

        assert svc.is_revoked("jti-redis-miss") is False

    def test_is_revoked_redis_error_falls_back(self) -> None:
        """is_revoked should fall back to in-memory when Redis raises."""
        mock_redis = MagicMock()
        mock_redis.exists.side_effect = ConnectionError("redis down")
        svc = _make_svc()
        svc.set_redis(mock_redis)

        # Should not raise; should check in-memory.
        assert svc.is_revoked("jti-redis-err") is False

    def test_is_revoked_redis_error_with_in_memory_hit(self) -> None:
        """is_revoked should find in-memory entry when Redis fails."""
        mock_redis = MagicMock()
        mock_redis.exists.side_effect = ConnectionError("redis down")
        svc = _make_svc()
        svc.set_redis(mock_redis)

        now = datetime.now(UTC).timestamp()
        svc._blacklist["jti-mem-hit"] = now
        assert svc.is_revoked("jti-mem-hit") is True


# ===========================================================================
# refresh_access_token
# ===========================================================================


class TestRefreshAccessToken:
    """Tests for refresh_access_token happy path and edge cases."""

    def test_refresh_returns_new_access_payload(self) -> None:
        """refresh_access_token should return a new short-lived access payload."""
        svc = _make_svc()
        refresh_token = svc.create_refresh_token(
            user_id="user-1", role=Role.EDITOR, permissions=["read"],
        )
        new_payload = svc.refresh_access_token(refresh_token)

        assert new_payload.sub == "user-1"
        assert new_payload.role == Role.EDITOR
        assert new_payload.permissions == ["read"]
        # Access token should have a short TTL (minutes, not days).
        ttl = new_payload.exp - new_payload.iat
        assert ttl <= timedelta(minutes=31)  # 30 min + small margin

    def test_refresh_with_revoked_token_fails(self) -> None:
        """refresh_access_token with a revoked refresh token should raise."""
        svc = _make_svc()
        refresh_token = svc.create_refresh_token(user_id="user-1")

        # Decode to get the jti, then revoke.
        import jwt
        data = jwt.decode(refresh_token, SECRET, algorithms=["HS256"], issuer="arrow-lake")
        svc.revoke_token(data["jti"])

        with pytest.raises(ValueError, match="revoked"):
            svc.refresh_access_token(refresh_token)


# ===========================================================================
# verify_token — require_refresh
# ===========================================================================


class TestVerifyTokenRequireRefresh:
    """Tests for verify_token with require_refresh=True."""

    def test_access_token_rejected_when_require_refresh(self) -> None:
        """Short-lived access token should be rejected when require_refresh=True."""
        svc = _make_svc(access_token_minutes=15)
        payload = svc.create_access_token(user_id="user-1")
        token = svc._encode(payload)

        with pytest.raises(ValueError, match="Expected refresh token"):
            svc.verify_token(token, require_refresh=True)

    def test_refresh_token_accepted_when_require_refresh(self) -> None:
        """Long-lived refresh token should pass require_refresh check."""
        svc = _make_svc(refresh_token_days=7)
        refresh_token = svc.create_refresh_token(user_id="user-1")

        # Should not raise.
        result = svc.verify_token(refresh_token, require_refresh=True)
        assert result.sub == "user-1"

    def test_token_missing_exp_raises_when_require_refresh(self) -> None:
        """Token without exp claim should raise when require_refresh=True."""
        svc = _make_svc()
        import jwt as pyjwt

        # PyJWT rejects tokens without exp, so this raises InvalidTokenError
        data = {
            "sub": "user-1",
            "role": "viewer",
            "permissions": [],
            "iat": int(datetime.now(UTC).timestamp()),
            "iss": "arrow-lake",
            "jti": "no-exp-jti",
        }
        token = pyjwt.encode(data, SECRET, algorithm="HS256")

        with pytest.raises(ValueError):
            svc.verify_token(token, require_refresh=True)


# ===========================================================================
# _validate_config
# ===========================================================================


class TestValidateConfig:
    """Tests for _validate_config with different algorithms."""

    def test_rs256_without_keys_raises(self) -> None:
        """RS256 algorithm without public/private keys should raise ValueError."""
        with pytest.raises(ValueError, match="requires both jwt_public_key and jwt_private_key"):
            _make_svc(algorithm="RS256", public_key="", private_key="")

    def test_es256_without_keys_raises(self) -> None:
        """ES256 algorithm without keys should raise ValueError."""
        with pytest.raises(ValueError, match="requires both jwt_public_key and jwt_private_key"):
            _make_svc(algorithm="ES256", public_key="", private_key="")

    def test_hs256_empty_secret_raises(self) -> None:
        """HS256 with empty secret should raise ValueError."""
        with pytest.raises(ValueError, match="secret_key is required"):
            _make_svc(secret_key="")


# ===========================================================================
# _encode
# ===========================================================================


class TestEncode:
    """Tests for _encode token encoding."""

    def test_encode_produces_valid_jwt(self) -> None:
        """_encode should produce a decodable JWT string."""
        import jwt

        svc = _make_svc()
        payload = svc.create_access_token(user_id="user-1", role=Role.ADMIN)
        token = svc._encode(payload)

        assert isinstance(token, str)
        decoded = jwt.decode(token, SECRET, algorithms=["HS256"], issuer="arrow-lake")
        assert decoded["sub"] == "user-1"
        assert decoded["role"] == "admin"

    def test_encode_without_pyjwt_raises(self) -> None:
        """_encode should raise ValueError when PyJWT is not available."""
        svc = _make_svc()
        payload = svc.create_access_token(user_id="user-1")

        with patch("arrow_lake.api.auth_service._JWT_AVAILABLE", False):
            with pytest.raises(ValueError, match="PyJWT not installed"):
                svc._encode(payload)
