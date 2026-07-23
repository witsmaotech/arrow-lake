"""Authentication endpoints: token exchange and refresh."""

from __future__ import annotations

import asyncio
import hmac
import logging
import time

from fastapi import APIRouter, HTTPException, Request

from arrow_lake.api.auth_models import LoginRequest, Role, TokenPair
from arrow_lake.api.deps import get_app_config
from arrow_lake.api._security_log import (
    LOGIN_FAILURE,
    LOGIN_SUCCESS,
    LOGOUT,
    log_security_event,
)
from arrow_lake.api.rate_limit import _extract_client_ip
from arrow_lake.config import ArrowLakeConfig

logger = logging.getLogger(__name__)

# v1.9.1: per-(username, client_ip) login failure lockout(防撞库;单进程内存,
# 多 worker 部署需迁 Redis/system_db follow-up)
_LOGIN_FAILURES: dict[str, list[float]] = {}
_LOGIN_FAIL_LIMIT = 10
_LOGIN_LOCKOUT_SECONDS = 900  # 15min
_LOGIN_EVICT_INTERVAL = 120.0
_login_lock = asyncio.Lock()
_last_login_evict = 0.0


def _client_ip(request: Request) -> str:
    # 复用 rate_limit 的 trusted-proxy-aware 提取(右起跳过可信代理,防 XFF leftmost 伪造绕 lockout)
    return _extract_client_ip(request, set())


def _evict_stale_login_failures(now: float) -> None:
    global _last_login_evict
    if now - _last_login_evict < _LOGIN_EVICT_INTERVAL:
        return
    _last_login_evict = now
    cutoff = now - _LOGIN_LOCKOUT_SECONDS
    stale = [k for k, v in _LOGIN_FAILURES.items() if not v or max(v) < cutoff]
    for k in stale:
        del _LOGIN_FAILURES[k]


async def _check_login_lockout(username: str, ip: str) -> None:
    key = f"{username}:{ip}"
    now = time.time()
    async with _login_lock:  # 原子:防 check/record 间并发 race
        _evict_stale_login_failures(now)
        fails = [t for t in _LOGIN_FAILURES.get(key, []) if t > now - _LOGIN_LOCKOUT_SECONDS]
        if len(fails) >= _LOGIN_FAIL_LIMIT:
            logger.warning("login_locked ip=%s failures=%d", ip, len(fails))
            raise HTTPException(status_code=429, detail="Too many login failures, try later")


async def _record_login_failure(username: str, ip: str) -> None:
    key = f"{username}:{ip}"
    now = time.time()
    async with _login_lock:
        fails = [t for t in _LOGIN_FAILURES.get(key, []) if t > now - _LOGIN_LOCKOUT_SECONDS]
        _LOGIN_FAILURES[key] = fails + [now]


router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _get_auth_service(request: Request):
    """Get or create AuthService from app state."""
    svc = getattr(request.app.state, "auth_service", None)
    if svc is not None:
        return svc
    # Fallback: create from config (for tests without full app setup)
    from arrow_lake.api.auth_service import AuthService

    config: ArrowLakeConfig = get_app_config(request)
    auth_cfg = config.auth
    return AuthService(
        secret_key=auth_cfg.jwt_secret_key,
        algorithm=auth_cfg.jwt_algorithm,
        public_key=auth_cfg.jwt_public_key,
        private_key=auth_cfg.jwt_private_key,
        access_token_minutes=auth_cfg.jwt_access_token_minutes,
        refresh_token_days=auth_cfg.jwt_refresh_token_days,
        issuer=auth_cfg.jwt_issuer,
    )


def _check_api_key(request: Request, config: ArrowLakeConfig) -> None:
    """Validate authentication for the token exchange endpoint.

    - auth_mode='both': validate API key header.
    - auth_mode='jwt': validate bootstrap token or existing refresh token.
    - auth_mode='api_key': reject (use API key directly for API calls).
    """
    from arrow_lake.config._enums import AuthMode

    mode = config.auth.auth_mode

    if mode == AuthMode.BOTH:
        api_key = config.api.api_key
        if not api_key:
            return
        header_name = config.api.api_key_header
        provided = request.headers.get(header_name, "")
        if not hmac.compare_digest(provided, api_key):
            raise HTTPException(status_code=401, detail="Invalid or missing API key")

    elif mode == AuthMode.JWT:
        # Check for bootstrap token in Authorization header
        auth_header = request.headers.get("Authorization", "")
        bootstrap_token = config.auth.jwt_bootstrap_token
        if bootstrap_token:
            # Accept as "Bearer <bootstrap_token>"
            provided = auth_header.removeprefix("Bearer ").strip() if auth_header else ""
            if hmac.compare_digest(provided, bootstrap_token):
                return
        # Also accept a valid refresh token as Bearer token
        if auth_header.startswith("Bearer "):
            svc = _get_auth_service(request)
            try:
                svc.verify_token(auth_header[7:], require_refresh=True)
                return
            except (ValueError, AttributeError):
                pass
        raise HTTPException(
            status_code=401,
            detail="Provide a valid bootstrap token or refresh token to obtain new JWT",
        )

    else:
        # auth_mode='api_key' — fail-closed: key must match when configured
        api_key = config.api.api_key
        if not api_key:
            # unauthenticated setup (no api_key AND no jwt secret) — allow
            if not config.auth.jwt_secret_key:
                return
            raise HTTPException(status_code=500, detail="api_key mode requires api_key to be configured")
        header_name = config.api.api_key_header
        provided = request.headers.get(header_name, "")
        if not hmac.compare_digest(provided, api_key):
            raise HTTPException(status_code=401, detail="Invalid or missing API key")


@router.post("/token", summary="Exchange credentials for JWT token pair")
async def exchange_token(request: Request) -> TokenPair:
    """Return JWT access + refresh tokens.

    auth_mode='both': requires X-API-Key header.
    auth_mode='jwt': requires bootstrap token or valid refresh token.
    auth_mode='api_key': returns 403.
    """
    config: ArrowLakeConfig = get_app_config(request)
    auth_cfg = config.auth

    if not auth_cfg.jwt_secret_key:
        raise HTTPException(status_code=500, detail="JWT secret key not configured")

    _check_api_key(request, config)

    svc = _get_auth_service(request)

    # Default to editor role for API-key-based token exchange
    payload = svc.create_access_token(user_id="api-user", role=Role.EDITOR)
    refresh = svc.create_refresh_token(
        user_id="api-user", role=Role.EDITOR, permissions=payload.permissions
    )

    return TokenPair(
        access_token=svc._encode(payload),
        refresh_token=refresh,
    )


@router.post("/login", summary="Login with username + password")
async def login_with_password(request: Request, creds: LoginRequest) -> TokenPair:
    """Verify username/password against the libSQL identity store, return JWT pair."""
    store = getattr(request.app.state, "identity_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="User auth requires system_db enabled")
    ip = _client_ip(request)
    await _check_login_lockout(creds.username, ip)
    user = store.get_user_with_credentials(creds.username)
    from arrow_lake.api.passwords import verify_password

    if (
        not user
        or not user.get("is_active")
        or not verify_password(creds.password, user.get("password_hash"))
    ):
        await _record_login_failure(creds.username, ip)
        logger.warning("login_failed ip=%s", ip)
        await log_security_event(
            LOGIN_FAILURE, creds.username,
            lake=getattr(request.app.state, "lake", None),
            detail={"ip": ip},
        )
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    try:
        role = Role(user["role"])
    except ValueError:
        role = Role.VIEWER
    svc = _get_auth_service(request)
    payload = svc.create_access_token(user_id=str(user["id"]), role=role, username=user.get("username"))
    refresh = svc.create_refresh_token(
        user_id=str(user["id"]), role=role, permissions=payload.permissions, username=user.get("username")
    )
    logger.info("login_success user_id=%s username=%s ip=%s", user["id"], creds.username, ip)
    await log_security_event(
        LOGIN_SUCCESS, creds.username,
        lake=getattr(request.app.state, "lake", None),
        detail={"user_id": user["id"], "ip": ip, "role": role.value},
    )
    return TokenPair(access_token=svc._encode(payload), refresh_token=refresh)


@router.post("/refresh", summary="Refresh access token")
async def refresh_token(request: Request) -> TokenPair:
    """Accept a refresh token and return a new token pair.

    The refresh token may be sent either as a ``Bearer`` token (the console's
    preferred form — see ``console/src/api.js`` ``doRefresh``) or in the JSON
    body as ``{"refresh_token": "..."}``. Both are accepted so the endpoint is
    reachable without a valid access token or API key (the whole point of a
    refresh is that the access token has expired).
    """
    refresh_token = ""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        refresh_token = auth_header[len("Bearer "):].strip()

    if not refresh_token:
        content_length = request.headers.get("content-length", "")
        if content_length:
            try:
                if int(content_length) > 10_240:
                    raise HTTPException(status_code=413, detail="Request body too large")
            except ValueError:
                pass
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 — empty/non-JSON body → 400, not 500
            body = None
        refresh_token = body.get("refresh_token") if isinstance(body, dict) else ""

    if not refresh_token or not isinstance(refresh_token, str):
        raise HTTPException(status_code=400, detail="refresh_token is required and must be a string")
    svc = _get_auth_service(request)

    try:
        new_payload = svc.refresh_access_token(refresh_token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    new_refresh = svc.create_refresh_token(
        user_id=new_payload.sub,
        role=new_payload.role,
        permissions=new_payload.permissions,
        username=new_payload.username,
    )

    return TokenPair(
        access_token=svc._encode(new_payload),
        refresh_token=new_refresh,
    )


@router.get("/me", summary="Get current user info")
async def get_me(request: Request) -> dict:
    """Return the current authenticated user's info from JWT."""
    from arrow_lake.api.deps import get_current_user

    user = get_current_user(request)

    return {
        "sub": user.sub,
        "role": user.role.value,
        "permissions": user.permissions,
        "iss": user.iss,
        "username": user.username,
    }


@router.post("/logout", summary="Revoke current token")
async def logout(request: Request) -> dict:
    """Revoke the provided JWT token by adding its jti to the blacklist."""
    from arrow_lake.api.deps import get_current_user

    user = get_current_user(request)
    if user.jti:
        svc = _get_auth_service(request)
        svc.revoke_token(user.jti)
    await log_security_event(
        LOGOUT, getattr(user, "username", None) or getattr(user, "sub", "?") or "unknown",
        lake=getattr(request.app.state, "lake", None),
    )
    return {"message": "Token revoked"}
