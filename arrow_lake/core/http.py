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


def _should_bypass_proxy(host: str) -> bool:
    """Check if *host* matches any pattern in NO_PROXY / no_proxy."""
    no_proxy = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    if not no_proxy:
        return False
    patterns = [p.strip() for p in no_proxy.split(",") if p.strip()]
    for pattern in patterns:
        if host == pattern or host.endswith("." + pattern):
            return True
        # CIDR ranges: 172.16.0.0/12 etc. — simple prefix match for common cases
        if "/" in pattern:
            try:
                import ipaddress

                network = ipaddress.ip_network(pattern, strict=False)
                addr = ipaddress.ip_address(host)
                if addr in network:
                    return True
            except (ValueError, OSError):
                pass
    return False


def _build_proxy_config(target_host: str | None = None) -> httpx.Proxy | dict[str, httpx.Proxy] | None:
    proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or os.environ.get("https_proxy") or os.environ.get("http_proxy")
    if not proxy_url:
        return None
    if target_host and _should_bypass_proxy(target_host):
        return None
    return proxy_url


def create_http_client(**kwargs: Any) -> httpx.Client:
    kwargs.setdefault("trust_env", False)
    if "proxy" not in kwargs and "proxies" not in kwargs:
        base = kwargs.get("base_url", "")
        host = _extract_host(base)
        proxy = _build_proxy_config(target_host=host)
        if proxy:
            kwargs["proxy"] = proxy
    return httpx.Client(**kwargs)  # type: ignore[arg-type]


def create_async_http_client(**kwargs: Any) -> httpx.AsyncClient:
    kwargs.setdefault("trust_env", False)
    if "proxy" not in kwargs and "proxies" not in kwargs:
        base = kwargs.get("base_url", "")
        host = _extract_host(base)
        proxy = _build_proxy_config(target_host=host)
        if proxy:
            kwargs["proxy"] = proxy
    return httpx.AsyncClient(**kwargs)  # type: ignore[arg-type]


def _extract_host(url: str) -> str | None:
    """Extract hostname from a URL string for NO_PROXY matching."""
    if not url:
        return None
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        return parsed.hostname
    except Exception:
        return None
