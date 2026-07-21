"""JWT authentication models: Role, TokenPayload, TokenPair."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class Role(StrEnum):
    """User roles for RBAC."""

    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"


class TokenPayload(BaseModel):
    """JWT token claims."""

    sub: str
    role: Role
    permissions: list[str] = []
    exp: datetime
    iat: datetime
    iss: str = "arrow-lake"
    jti: str = ""
    username: str | None = None


class TokenPair(BaseModel):
    """Access + refresh token pair returned from auth endpoints."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class LoginRequest(BaseModel):
    """Username + password credentials for /auth/login."""

    username: str
    password: str
