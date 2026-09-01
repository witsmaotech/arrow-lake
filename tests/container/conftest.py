"""Shared fixtures for container smoke tests.

Service endpoints align with docker-compose.prod.yml env vars:
  - API:      ARROW_LAKE__API__HOST / ARROW_LAKE__API__PORT
  - Ollama:   ARROW_LAKE__EMBEDDING__API_BASE
  - HugeGraph: ARROW_LAKE__HUGEGRAPH__HOST / ARROW_LAKE__HUGEGRAPH__PORT

Detects service availability and provides session-scoped test data that
is created once and cleaned up after all tests.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess

import httpx
import pytest

from tests.conftest_services import (
    API_BASE_URL,
    API_KEY,
    OLLAMA_API_BASE,
    api_reachable,
    hugegraph_reachable,
    ollama_reachable,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATASET_NAME = "smoke-test"
API_TIMEOUT = 60

# Resolve container name: Compose V2 appends -1 when container_name is omitted.
_container_base = "arrow-lake-api"
_resolved = subprocess.run(
    ["docker", "ps", "--format", "{{.Names}}"],
    capture_output=True, text=True, timeout=10,
)
_api_container = _container_base
for _name in _resolved.stdout.splitlines():
    if _name.startswith(_container_base):
        _api_container = _name
        break

_SMOKE_ROWS = 300
_SMOKE_TEXTS = [
    "Machine learning is a subset of artificial intelligence.",
    "Deep learning uses neural networks with many layers.",
    "Python is a popular programming language for data science.",
    "Vector databases enable fast similarity search.",
    "Knowledge graphs store entities and their relationships.",
    "Natural language processing helps computers understand human language.",
    "Reinforcement learning trains agents through rewards and penalties.",
    "Transfer learning applies knowledge from one domain to another.",
    "Computer vision enables machines to interpret visual information.",
    "Generative models can create new data samples from learned distributions.",
]
_SMOKE_SOURCES = ["ai-intro", "lang-intro", "db-intro", "kg-intro", "cv-intro"]


def _generate_smoke_jsonl() -> str:
    lines = []
    for i in range(_SMOKE_ROWS):
        lines.append(json.dumps({
            "text_content": _SMOKE_TEXTS[i % len(_SMOKE_TEXTS)],
            "source": _SMOKE_SOURCES[i % len(_SMOKE_SOURCES)],
            "doc_type": "article",
        }))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Service reachability (delegated to shared conftest_services)
# ---------------------------------------------------------------------------

# NOTE (v1.11.5-W1): the reachability/auth gate lives as a module-level
# ``pytestmark = require_live_api`` in test_container_smoke.py — a
# ``pytestmark`` assignment in conftest.py is silently ignored by pytest, so
# the previous API-unreachable skip defined here never actually applied.

# Markers for external services (using shared reachability checks)
require_ollama = pytest.mark.skipif(
    not ollama_reachable(),
    reason=f"Ollama not reachable at {OLLAMA_API_BASE}",
)

require_hugegraph = pytest.mark.skipif(
    not hugegraph_reachable(),
    reason="HugeGraph not reachable (set ARROW_LAKE__HUGEGRAPH__HOST/PORT)",
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def client() -> httpx.Client:
    """HTTP client authenticated with API key."""
    return httpx.Client(
        base_url=API_BASE_URL,
        headers={"X-API-Key": API_KEY},
        timeout=API_TIMEOUT,
    )


@pytest.fixture(scope="session")
def test_data(client: httpx.Client) -> str:
    """Create a test dataset with 300 rows, yield its name, clean up after session."""
    container = _api_container
    jsonl_path = f"/tmp/{DATASET_NAME}.jsonl"

    jsonl_content = _generate_smoke_jsonl()
    result = subprocess.run(
        ["docker", "exec", "-i", container, "sh", "-c", f"cat > {jsonl_path}"],
        input=jsonl_content.encode(),
        capture_output=True,
        timeout=30,
    )
    if result.returncode != 0:
        pytest.fail(f"Failed to write test data to container: {result.stderr.decode()}")

    with contextlib.suppress(Exception):
        client.delete(f"/api/v1/datasets/{DATASET_NAME}")

    resp = client.post(
        f"/api/v1/datasets/{DATASET_NAME}/ingest",
        json={"file_paths": [jsonl_path]},
    )
    assert resp.status_code in (200, 201), f"Ingest failed: {resp.text}"

    body = resp.json()
    assert body.get("success"), f"Ingest error: {body}"

    yield DATASET_NAME

    with contextlib.suppress(Exception):
        client.delete(f"/api/v1/datasets/{DATASET_NAME}")


@pytest.fixture(scope="session")
def embedded_data(client: httpx.Client, test_data: str) -> str:
    """Generate text_embedding column for the dataset via Ollama embedding API."""
    r = client.post(
        f"/api/v1/datasets/{test_data}/query/olap",
        json={"sql": f'SELECT text_content, source, doc_type FROM "{test_data}"', "format": "json"},
    )
    if r.status_code != 200:
        pytest.skip(f"Cannot read dataset for embedding: {r.text[:100]}")

    rows = r.json().get("rows", [])
    if not rows:
        pytest.skip("Dataset is empty, nothing to embed")

    all_embeddings: list[list[float]] = []
    batch_size = 64
    for i in range(0, len(rows), batch_size):
        batch_texts = [row.get("text_content", "") or "" for row in rows[i : i + batch_size]]
        emb_r = client.post(
            "/api/v1/embed/text",
            json={"texts": batch_texts, "model": "nomic-embed-text-v2-moe"},
        )
        if emb_r.status_code != 200:
            pytest.skip(f"Embedding API failed: {emb_r.text[:100]}")
        batch_emb = emb_r.json().get("embeddings", [])
        if not batch_emb:
            pytest.skip("Embedding API returned empty results")
        all_embeddings.extend(batch_emb)

    container = _api_container
    emb_jsonl_path = f"/tmp/{test_data}-embedded.jsonl"

    lines = []
    for row, emb in zip(rows, all_embeddings, strict=False):
        obj = {
            "text_content": row.get("text_content", ""),
            "source": row.get("source", ""),
            "doc_type": row.get("doc_type", "article"),
            "text_embedding": emb,
        }
        lines.append(json.dumps(obj))

    jsonl_content = "\n".join(lines)
    result = subprocess.run(
        ["docker", "exec", "-i", container, "sh", "-c", f"cat > {emb_jsonl_path}"],
        input=jsonl_content.encode(),
        capture_output=True,
        timeout=30,
    )
    if result.returncode != 0:
        pytest.skip(f"Failed to write embedded data: {result.stderr.decode()[:100]}")

    client.delete(f"/api/v1/datasets/{test_data}")

    resp = client.post(
        f"/api/v1/datasets/{test_data}/ingest",
        json={"file_paths": [emb_jsonl_path]},
    )
    if resp.status_code not in (200, 201):
        pytest.skip(f"Re-ingest with embeddings failed: {resp.text[:100]}")

    yield test_data


@pytest.fixture(scope="session")
def kg_test_data(client: httpx.Client) -> str:
    """Small dataset (10 rows) for KG build tests to avoid LLM timeout."""
    name = "smoke-test-kg"
    container = _api_container
    jsonl_path = f"/tmp/{name}.jsonl"

    _KG_ROWS = [
        {"text_content": "Apple is a technology company founded by Steve Jobs.", "source": "tech", "doc_type": "article"},
        {"text_content": "Google developed the Transformer architecture for NLP.", "source": "tech", "doc_type": "article"},
        {"text_content": "Microsoft created the Windows operating system.", "source": "tech", "doc_type": "article"},
        {"text_content": "Amazon Web Services provides cloud computing platforms.", "source": "tech", "doc_type": "article"},
        {"text_content": "Meta owns Facebook, Instagram, and WhatsApp.", "source": "tech", "doc_type": "article"},
        {"text_content": "Tesla produces electric vehicles and battery systems.", "source": "tech", "doc_type": "article"},
        {"text_content": "NVIDIA designs GPUs for AI and deep learning.", "source": "tech", "doc_type": "article"},
        {"text_content": "OpenAI created the GPT series of language models.", "source": "tech", "doc_type": "article"},
        {"text_content": "Linux is the most widely used open source operating system.", "source": "tech", "doc_type": "article"},
        {"text_content": "Python is a high-level programming language.", "source": "tech", "doc_type": "article"},
    ]

    lines = [json.dumps(row) for row in _KG_ROWS]
    jsonl_content = "\n".join(lines)

    with contextlib.suppress(Exception):
        client.delete(f"/api/v1/datasets/{name}")

    result = subprocess.run(
        ["docker", "exec", "-i", container, "sh", "-c", f"cat > {jsonl_path}"],
        input=jsonl_content.encode(),
        capture_output=True,
        timeout=30,
    )
    if result.returncode != 0:
        pytest.fail(f"Failed to write KG test data: {result.stderr.decode()}")

    resp = client.post(
        f"/api/v1/datasets/{name}/ingest",
        json={"file_paths": [jsonl_path]},
    )
    if resp.status_code not in (200, 201):
        pytest.fail(f"KG test data ingest failed: {resp.text}")

    yield name

    with contextlib.suppress(Exception):
        client.delete(f"/api/v1/datasets/{name}")
