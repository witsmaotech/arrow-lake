"""Shared httpx client factory — explicit proxy support.

In WSL2 mirrored mode, containers need an explicit proxy to reach the internet.
The proxy is configured via HTTP_PROXY/HTTPS_PROXY env vars set by docker-compose,
with NO_PROXY excluding all internal service hostnames.

trust_env=False prevents accidental proxy inheritance from the host, but we
explicitly read and apply proxy config when the env vars are set by compose.
"""

from __future__ import annotations

import os
from typing import Any

import httpx


def _build_proxy_config() -> httpx.Proxy | dict[str, httpx.Proxy] | None:
    proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or os.environ.get("https_proxy") or os.environ.get("http_proxy")
    if not proxy_url:
        return None
    return proxy_url


def create_http_client(**kwargs: Any) -> httpx.Client:
    kwargs.setdefault("trust_env", False)
    if "proxy" not in kwargs and "proxies" not in kwargs:
        proxy = _build_proxy_config()
        if proxy:
            kwargs["proxy"] = proxy
    return httpx.Client(**kwargs)  # type: ignore[arg-type]


def create_async_http_client(**kwargs: Any) -> httpx.AsyncClient:
    kwargs.setdefault("trust_env", False)
    if "proxy" not in kwargs and "proxies" not in kwargs:
        proxy = _build_proxy_config()
        if proxy:
            kwargs["proxy"] = proxy
    return httpx.AsyncClient(**kwargs)  # type: ignore[arg-type]
