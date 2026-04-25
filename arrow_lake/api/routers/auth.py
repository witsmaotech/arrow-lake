"""Authentication endpoints: token exchange and refresh."""

from __future__ import annotations

import hmac

from fastapi import APIRouter, HTTPException, Request

from arrow_lake.api.auth_models import Role, TokenPair
from arrow_lake.api.deps import get_app_config
from arrow_lake.config import ArrowLakeConfig

router = APIRouter(prefix="/api/v2/auth", tags=["auth"])


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
        access_token_minutes=auth_cfg.jwt_access_token_minutes,
        refresh_token_days=auth_cfg.jwt_refresh_token_days,
        issuer=auth_cfg.jwt_issuer,
    )


def _check_api_key(request: Request, config: ArrowLakeConfig) -> None:
    """Validate API key when auth_mode is 'both'.

    Raises HTTPException 401 if API key is required but missing/invalid.
    """
    if config.auth.auth_mode != "both":
        return
    api_key = config.api.api_key
    if not api_key:
        return
    header_name = config.api.api_key_header
    provided = request.headers.get(header_name, "")
    if not hmac.compare_digest(provided, api_key):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


@router.post("/token", summary="Exchange API key for JWT token pair")
async def exchange_token(request: Request) -> TokenPair:
    """Accept API key, return JWT access + refresh tokens.

    Requires X-API-Key header when auth_mode is "both".
    When auth_mode is "jwt", any request gets a token (for initial bootstrap).
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


@router.post("/refresh", summary="Refresh access token")
async def refresh_token(request: Request) -> TokenPair:
    """Accept a refresh token in JSON body, return a new token pair."""
    content_length = request.headers.get("content-length", "")
    if content_length:
        try:
            if int(content_length) > 10_240:
                raise HTTPException(status_code=413, detail="Request body too large")
        except ValueError:
            pass
    body = await request.json()
    refresh_token = body.get("refresh_token") if isinstance(body, dict) else None
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
    }
