"""Shared fixtures for container smoke tests.

Detects service availability (API, Ollama, HugeGraph) and provides
session-scoped test data that is created once and cleaned up after all tests.
"""

from __future__ import annotations

import json
import os
import subprocess

import httpx
import pytest

# ---------------------------------------------------------------------------
# Configuration (from environment or defaults)
# ---------------------------------------------------------------------------

API_BASE = os.getenv("ARROW_LAKE_API_URL", "http://localhost:8000")
API_KEY = os.getenv("ARROW_LAKE_API_KEY", "dev-api-key-for-testing")
DATASET_NAME = "smoke-test"

# External services
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://192.168.101.131:11434")
HUGEGRAPH_URL = os.getenv("HUGEGRAPH_URL", "http://localhost:8091")

# Default timeout for API calls (seconds)
API_TIMEOUT = 60

# Test data: 300 rows — enough for vector index (min 256 rows required).
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
    """Generate 300-row JSONL string for test data ingestion."""
    lines = []
    for i in range(_SMOKE_ROWS):
        lines.append(json.dumps({
            "text_content": _SMOKE_TEXTS[i % len(_SMOKE_TEXTS)],
            "source": _SMOKE_SOURCES[i % len(_SMOKE_SOURCES)],
            "doc_type": "article",
        }))
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# Service readiness checks (cached at import time for skip decisions)
# ---------------------------------------------------------------------------

_api_reachable: bool | None = None
_ollama_reachable: bool | None = None
_hugegraph_reachable: bool | None = None


def _check_api() -> bool:
    try:
        r = httpx.get(f"{API_BASE}/health", timeout=5)
        return r.status_code in (200, 503)
    except Exception:
        return False


def _check_ollama() -> bool:
    try:
        r = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def _check_hugegraph() -> bool:
    try:
        r = httpx.get(f"{HUGEGRAPH_URL}/graphs", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def api_reachable() -> bool:
    global _api_reachable
    if _api_reachable is None:
        _api_reachable = _check_api()
    return _api_reachable


def ollama_reachable() -> bool:
    global _ollama_reachable
    if _ollama_reachable is None:
        _ollama_reachable = _check_ollama()
    return _ollama_reachable


def hugegraph_reachable() -> bool:
    global _hugegraph_reachable
    if _hugegraph_reachable is None:
        _hugegraph_reachable = _check_hugegraph()
    return _hugegraph_reachable


# Module-level skip: skip ALL container tests if API is not reachable
pytestmark = pytest.mark.skipif(
    not api_reachable(),
    reason=f"API not reachable at {API_BASE} (start with: make up)",
)

# Markers for external services
require_ollama = pytest.mark.skipif(
    not ollama_reachable(),
    reason=f"Ollama not reachable at {OLLAMA_URL}",
)

require_hugegraph = pytest.mark.skipif(
    not hugegraph_reachable(),
    reason=f"HugeGraph not reachable at {HUGEGRAPH_URL}",
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def client() -> httpx.Client:
    """HTTP client authenticated with API key."""
    return httpx.Client(
        base_url=API_BASE,
        headers={"X-API-Key": API_KEY},
        timeout=API_TIMEOUT,
    )


@pytest.fixture(scope="session")
def test_data(client: httpx.Client) -> str:
    """Create a test dataset with 300 rows, yield its name, clean up after session."""
    container = "arrow-lake-api"
    jsonl_path = f"/tmp/{DATASET_NAME}.jsonl"

    # Generate JSONL on host and pipe into container via docker exec
    jsonl_content = _generate_smoke_jsonl()
    result = subprocess.run(
        ["docker", "exec", "-i", container, "sh", "-c", f"cat > {jsonl_path}"],
        input=jsonl_content.encode(),
        capture_output=True,
        timeout=30,
    )
    if result.returncode != 0:
        pytest.fail(f"Failed to write test data to container: {result.stderr.decode()}")

    # Clean up any leftover dataset from previous runs
    try:
        client.delete(f"/api/v1/datasets/{DATASET_NAME}")
    except Exception:
        pass

    # Ingest
    resp = client.post(
        f"/api/v1/datasets/{DATASET_NAME}/ingest",
        json={"file_paths": [jsonl_path]},
    )
    assert resp.status_code in (200, 201), f"Ingest failed: {resp.text}"

    body = resp.json()
    assert body.get("success"), f"Ingest error: {body}"

    yield DATASET_NAME

    # Cleanup: delete the dataset
    try:
        client.delete(f"/api/v1/datasets/{DATASET_NAME}")
    except Exception:
        pass


@pytest.fixture(scope="session")
def embedded_data(client: httpx.Client, test_data: str) -> str:
    """Generate text_embedding column for the dataset via Ollama embedding API.

    Reads all text_content, batch-embeds via /api/v1/embed/text, then
    re-ingests with embedding column included.
    Requires Ollama to be reachable.
    """
    # 1. Read all text_content from the dataset
    r = client.post(
        f"/api/v1/datasets/{test_data}/query/olap",
        json={"sql": f'SELECT text_content, source, doc_type FROM "{test_data}"', "format": "json"},
    )
    if r.status_code != 200:
        pytest.skip(f"Cannot read dataset for embedding: {r.text[:100]}")

    rows = r.json().get("rows", [])
    if not rows:
        pytest.skip("Dataset is empty, nothing to embed")

    # 2. Batch embed via Arrow Lake API
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

    # 3. Build JSONL with embeddings and re-ingest
    container = "arrow-lake-api"
    emb_jsonl_path = f"/tmp/{test_data}-embedded.jsonl"

    lines = []
    for row, emb in zip(rows, all_embeddings):
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

    # Delete old dataset and re-ingest with embeddings
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
    container = "arrow-lake-api"
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

    # Clean up leftover
    try:
        client.delete(f"/api/v1/datasets/{name}")
    except Exception:
        pass

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

    try:
        client.delete(f"/api/v1/datasets/{name}")
    except Exception:
        pass
