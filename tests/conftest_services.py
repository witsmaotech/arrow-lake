"""Shared service fixtures aligned with docker-compose.prod.yml.

Provides Docker Compose-aware service discovery, HugeGraph helpers,
LLM provider skip logic, and unified skip markers.

Service endpoints match docker-compose.prod.yml env vars:
  - ARROW_LAKE__HUGEGRAPH__HOST / ARROW_LAKE__HUGEGRAPH__PORT
  - ARROW_LAKE__EMBEDDING__API_BASE (Ollama)
  - ARROW_LAKE__API__HOST / ARROW_LAKE__API__PORT
"""

from __future__ import annotations

import os
import socket

import httpx
import pytest

# ---------------------------------------------------------------------------
# HugeGraph configuration (matches docker-compose.prod.yml)
# ---------------------------------------------------------------------------

HUGEGRAPH_HOST = os.getenv("ARROW_LAKE__HUGEGRAPH__HOST", os.getenv("HUGEGRAPH_HOST", "localhost"))
HUGEGRAPH_PORT = int(
    os.getenv("ARROW_LAKE__HUGEGRAPH__PORT", os.getenv("HUGEGRAPH_PORT", "8089"))
)
HUGEGRAPH_GRAPH = os.getenv("HUGEGRAPH_GRAPH", "hugegraph")
HUGEGRAPH_BASE_URL = f"http://{HUGEGRAPH_HOST}:{HUGEGRAPH_PORT}"
HUGEGRAPH_GRAPH_BASE = f"/graphs/{HUGEGRAPH_GRAPH}"

# ---------------------------------------------------------------------------
# LLM / Embedding configuration (matches docker-compose.prod.yml)
# ---------------------------------------------------------------------------

OLLAMA_API_BASE = os.getenv(
    "ARROW_LAKE__EMBEDDING__API_BASE",
    os.getenv("OLLAMA_API_BASE", "http://localhost:11434/v1"),
)
VLLM_API_BASE = os.getenv("VLLM_API_BASE", "http://localhost:8000/v1")

# ---------------------------------------------------------------------------
# API configuration (matches docker-compose.prod.yml)
# ---------------------------------------------------------------------------

API_HOST = os.getenv("ARROW_LAKE__API__HOST", "localhost")
API_PORT = int(os.getenv("ARROW_LAKE__API__PORT", "8000"))
API_BASE_URL = f"http://{API_HOST}:{API_PORT}"
API_KEY = os.getenv("ARROW_LAKE_API_KEY", "dev-api-key-for-local-testing-only")

# ---------------------------------------------------------------------------
# Service reachability checks
# ---------------------------------------------------------------------------


def _is_reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, ConnectionRefusedError):
        return False


def _http_reachable(url: str, path: str = "/", timeout: float = 5.0) -> bool:
    try:
        r = httpx.get(f"{url}{path}", timeout=timeout)
        return r.status_code < 500
    except Exception:
        return False


def hugegraph_reachable() -> bool:
    # Require a 200, not just <500. HugeGraph with auth enabled returns 401
    # to the unauthenticated ``make_hg_client`` used by tests; a 401 means
    # "service up but test client has no credentials" → treat as unreachable
    # so ``require_hugegraph`` skips instead of every request failing 401.
    try:
        r = httpx.get(
            f"{HUGEGRAPH_BASE_URL}/graphs/{HUGEGRAPH_GRAPH}/schema", timeout=5.0
        )
        return r.status_code == 200
    except Exception:
        return False


def ollama_reachable() -> bool:
    base = OLLAMA_API_BASE.replace("/v1", "")
    return _http_reachable(base, "/api/tags")


def vllm_reachable() -> bool:
    base = VLLM_API_BASE.replace("/v1", "")
    return _http_reachable(base, "/v1/models")


def api_reachable() -> bool:
    return _http_reachable(API_BASE_URL, "/health")


def api_auth_ok() -> bool:
    """The configured API key actually authenticates against the live API.

    v1.11.5-W1: the live stack's key was rotated (2026-08-17), so the default
    dev key gets 401 on every request — reachability alone is not a sufficient
    precondition for authenticated live-stack suites.
    """
    try:
        r = httpx.get(
            f"{API_BASE_URL}/api/v1/datasets?limit=1",
            headers={"X-API-Key": API_KEY},
            timeout=5,
        )
        return r.status_code == 200
    except Exception:
        return False


def has_vllm_api_key() -> bool:
    return bool(os.getenv("VLLM_API_KEY", os.getenv("OPENAI_API_KEY", "")))


# ---------------------------------------------------------------------------
# Skip markers for external services
# ---------------------------------------------------------------------------

require_hugegraph = pytest.mark.skipif(
    not hugegraph_reachable(),
    reason=f"HugeGraph not reachable at {HUGEGRAPH_BASE_URL}",
)

require_ollama = pytest.mark.skipif(
    not ollama_reachable(),
    reason=f"Ollama not reachable at {OLLAMA_API_BASE}",
)

require_vllm = pytest.mark.skipif(
    not vllm_reachable() or not has_vllm_api_key(),
    reason=f"vLLM not reachable at {VLLM_API_BASE} or API key missing",
)

require_api = pytest.mark.skipif(
    not api_reachable(),
    reason=f"API not reachable at {API_BASE_URL}",
)

# Authenticated live-API gate (v1.11.5-W1): reachable AND the configured key
# authenticates. Apply as a MODULE-level pytestmark in the test module —
# pytestmark in conftest.py is silently ignored by pytest.
require_live_api = pytest.mark.skipif(
    not (api_reachable() and api_auth_ok()),
    reason=(
        f"live API at {API_BASE_URL} unreachable, or ARROW_LAKE_API_KEY does "
        "not authenticate (set the rotated key to run live-stack suites)"
    ),
)

# ---------------------------------------------------------------------------
# HugeGraph Gremlin helpers (HugeGraph 1.7.0 compatible)
# ---------------------------------------------------------------------------


def gremlin_available(client: httpx.Client) -> bool:
    """Check if the Gremlin endpoint works in this HugeGraph version.

    HugeGraph 1.7.0 may not register traversal source variables,
    causing ``No such property: hugegraph for class: Script24`` errors.
    """
    try:
        resp = client.post("/gremlin", json={"gremlin": "1+1"}, timeout=5.0)
        if resp.status_code != 200:
            return False
        if "No such property" in resp.text:
            return False
        return True
    except Exception:
        return False


def gremlin(client: httpx.Client, query: str) -> dict:
    """Execute a Gremlin query via POST /gremlin.

    HugeGraph 1.7.0: endpoint is /gremlin, traversal source is
    ``{graph_name}.traversal()`` (not ``g.V()``).

    Skips gracefully when the Gremlin engine cannot resolve the
    traversal source.
    """
    resp = client.post("/gremlin", json={"gremlin": query})
    if resp.status_code != 200:
        body = resp.text
        if "No such property" in body:
            pytest.skip(
                "Gremlin unavailable — traversal source not registered in HugeGraph 1.7.0"
            )
        assert False, f"Gremlin query failed ({resp.status_code}): {body}"
    return resp.json()


# ---------------------------------------------------------------------------
# Shared httpx client factories (use inside local fixtures)
# ---------------------------------------------------------------------------


def make_hg_client(timeout: float = 30.0) -> httpx.Client:
    """Create an httpx client for HugeGraph tests."""
    return httpx.Client(base_url=HUGEGRAPH_BASE_URL, timeout=timeout)


def make_api_client(timeout: float = 60.0) -> httpx.Client:
    """Create an HTTP client authenticated with API key."""
    return httpx.Client(
        base_url=API_BASE_URL,
        headers={"X-API-Key": API_KEY},
        timeout=timeout,
    )
