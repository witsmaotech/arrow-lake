"""Shared httpx client factory — disables env-proxy by default.

All outbound HTTP from Arrow Lake services should use these factories so that
host proxy environment variables (HTTP_PROXY, HTTPS_PROXY) are never
accidentally inherited inside Docker containers.

If a future deployment needs an explicit proxy, configure it here centrally.
"""

from __future__ import annotations

import httpx


def create_http_client(**kwargs: object) -> httpx.Client:
    kwargs.setdefault("trust_env", False)
    return httpx.Client(**kwargs)  # type: ignore[arg-type]


def create_async_http_client(**kwargs: object) -> httpx.AsyncClient:
    kwargs.setdefault("trust_env", False)
    return httpx.AsyncClient(**kwargs)  # type: ignore[arg-type]
