"""Live integration test for kg_build + RAG against running API.

Usage:
    python tests/e2e/test_v162_live.py --api http://localhost:8000 --api-key <KEY>

As pytest tests they are opt-in: they probe the LIVE stack with a real key
(``ARROW_LAKE_API_KEY`` env) and build a KG on a real dataset — without both
they cannot validate anything and are skipped (v1.11.5-W1 suite-stability
gating; the module doubles as a manual script via argparse below).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import pytest

_LIVE_API_KEY = os.environ.get("ARROW_LAKE_API_KEY", "")

# cheap reachability probe — no auth, no side effects
def _api_reachable(base: str = "http://localhost:8000") -> bool:
    try:
        from urllib.request import Request, urlopen

        with urlopen(Request(f"{base}/health"), timeout=2):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not (_LIVE_API_KEY and _api_reachable()),
    reason="live-stack probe: set ARROW_LAKE_API_KEY and start the API to run",
)
from urllib.request import Request, urlopen

BASE = "http://localhost:8000"
API_KEY = ""


def _headers():
    h = {"Content-Type": "application/json"}
    if API_KEY:
        h["X-API-Key"] = API_KEY
    return h


def _get(path: str) -> dict:
    req = Request(f"{BASE}{path}", headers=_headers())
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _post(path: str, body: dict | None = None) -> dict:
    data = json.dumps(body or {}).encode()
    req = Request(f"{BASE}{path}", data=data, headers=_headers(), method="POST")
    with urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())


def test_health():
    """1. Health check + version."""
    t0 = time.time()
    h = _get("/health")
    elapsed = time.time() - t0
    print(f"[HEALTH] status={h['status']} version={h['version']} ({elapsed:.3f}s)")
    assert h["status"] == "ok"
    assert h["version"] == "1.6.2", f"Expected 1.6.2, got {h['version']}"
    return True


def test_datasets():
    """2. List available datasets."""
    t0 = time.time()
    r = _get("/api/v1/datasets")
    elapsed = time.time() - t0
    datasets = r.get("datasets", [])
    print(f"[DATASETS] count={len(datasets)} ({elapsed:.3f}s)")
    for d in datasets[:5]:
        print(f"  - {d['name']} ({d.get('row_count', '?')} rows)")
    return datasets


def test_kg_build(dataset_name: str = "knowledge_base", timeout: int = 300):
    """3. Trigger kg_build and poll until completion."""
    print(f"\n[KG_BUILD] Starting build for '{dataset_name}'...")
    t0 = time.time()

    try:
        r = _post(f"/api/v1/kg/build", {"dataset": dataset_name})
    except Exception as e:
        print(f"[KG_BUILD] Build request failed: {e}")
        print("[KG_BUILD] Dataset may not exist — listing datasets for alternatives:")
        ds = _get("/api/v1/datasets").get("datasets", [])
        for d in ds:
            print(f"  - {d['name']}")
        if ds:
            dataset_name = ds[0]["name"]
            print(f"[KG_BUILD] Retrying with '{dataset_name}'...")
            r = _post(f"/api/v1/kg/build", {"dataset": dataset_name})
        else:
            print("[KG_BUILD] No datasets available — skipping kg_build")
            return False

    task_id = r.get("task_id")
    print(f"[KG_BUILD] task_id={task_id} ({time.time()-t0:.3f}s)")

    # Poll status
    while True:
        elapsed = time.time() - t0
        if elapsed > timeout:
            print(f"[KG_BUILD] TIMEOUT after {timeout}s")
            return False

        try:
            status = _get(f"/api/v1/kg/build/{task_id}/status")
        except Exception:
            # Try TaskManager endpoint
            try:
                tasks = _get("/api/v1/tasks")
                matching = [t for t in tasks.get("tasks", []) if t.get("detail", {}).get("kg_task_id") == task_id]
                if matching:
                    tm = matching[0]
                    status = {"status": tm["status"], "processed_chunks": "?", "total_chunks": "?"}
                else:
                    status = {"status": "unknown"}
            except Exception:
                status = {"status": "unknown"}

        s = status.get("status", "?")
        processed = status.get("processed_chunks", "?")
        total = status.get("total_chunks", "?")
        entities = status.get("entity_count", 0)
        relations = status.get("relation_count", 0)

        print(f"  [{elapsed:.1f}s] status={s} chunks={processed}/{total} entities={entities} relations={relations}")

        if s in ("COMPLETED", "completed"):
            total_time = time.time() - t0
            print(f"[KG_BUILD] ✅ COMPLETED in {total_time:.2f}s")
            print(f"  entities={entities} relations={relations}")
            return True
        elif s in ("FAILED", "failed"):
            print(f"[KG_BUILD] ❌ FAILED: {status.get('error', 'unknown')}")
            return False

        time.sleep(5)


def test_kg_stats():
    """4. Get KG statistics."""
    try:
        t0 = time.time()
        stats = _get("/api/v1/kg/stats")
        elapsed = time.time() - t0
        print(f"\n[KG_STATS] {json.dumps(stats)} ({elapsed:.3f}s)")
        return True
    except Exception as e:
        print(f"[KG_STATS] Failed: {e}")
        return False


def test_kg_query():
    """5. Query knowledge graph."""
    try:
        t0 = time.time()
        r = _post("/api/v1/kg/query", {"query": "g.V().count()"})
        elapsed = time.time() - t0
        print(f"\n[KG_QUERY] result={json.dumps(r)[:200]} ({elapsed:.3f}s)")
        return True
    except Exception as e:
        print(f"[KG_QUERY] Failed: {e}")
        return False


def test_rag_query():
    """6. RAG query pipeline."""
    try:
        t0 = time.time()
        r = _post("/api/v1/rag/query", {
            "query": "What is Arrow Lake?",
            "top_k": 5,
        })
        elapsed = time.time() - t0
        answer = r.get("answer", r.get("response", str(r)))[:200]
        print(f"\n[RAG_QUERY] ({elapsed:.3f}s)")
        print(f"  answer: {answer}...")
        return True
    except Exception as e:
        print(f"[RAG_QUERY] Failed: {e}")
        return False


def test_rag_stream():
    """7. RAG streaming query."""
    try:
        t0 = time.time()
        r = _post("/api/v1/rag/stream", {
            "query": "Explain the data lakehouse architecture",
            "top_k": 3,
        })
        elapsed = time.time() - t0
        print(f"\n[RAG_STREAM] ({elapsed:.3f}s)")
        print(f"  response preview: {str(r)[:200]}...")
        return True
    except Exception as e:
        print(f"[RAG_STREAM] Failed: {e}")
        return False


def test_task_list():
    """8. List tasks (verify Redis state sharing)."""
    try:
        t0 = time.time()
        r = _get("/api/v1/tasks")
        elapsed = time.time() - t0
        tasks = r.get("tasks", [])
        print(f"\n[TASKS] total={r.get('total', 0)} ({elapsed:.3f}s)")
        for t in tasks[:5]:
            print(f"  - {t['task_id'][:12]}... op={t['operation']} status={t['status']}")
        return True
    except Exception as e:
        print(f"[TASKS] Failed: {e}")
        return False


def main():
    global BASE, API_KEY
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--dataset", default="knowledge_base")
    args = parser.parse_args()
    BASE = args.api
    API_KEY = args.api_key

    print("=" * 60)
    print(f"Arrow Lake v1.6.2 Live Integration Test")
    print(f"API: {BASE}")
    print("=" * 60)

    results = {}
    total_t0 = time.time()

    # 1. Health
    results["health"] = test_health()

    # 2. Datasets
    datasets = test_datasets()

    # 3. KG Build
    results["kg_build"] = test_kg_build(dataset_name=args.dataset)

    # 4. KG Stats
    results["kg_stats"] = test_kg_stats()

    # 5. KG Query
    results["kg_query"] = test_kg_query()

    # 6. RAG Query
    results["rag_query"] = test_rag_query()

    # 7. RAG Stream
    results["rag_stream"] = test_rag_stream()

    # 8. Task List (Redis sharing)
    results["task_list"] = test_task_list()

    total_elapsed = time.time() - total_t0
    print("\n" + "=" * 60)
    print(f"Results ({total_elapsed:.2f}s total):")
    for name, ok in results.items():
        icon = "✅" if ok else "❌"
        print(f"  {icon} {name}")
    print("=" * 60)

    all_pass = all(results.values())
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
