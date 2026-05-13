#!/usr/bin/env python3
"""API-01 — Health, Auth & Dataset CRUD

对应 cookbook: 01_ingest_basics.py, 08_catalog_management.py
验证: 健康检查、认证、数据集列表/详情/删除
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import ArrowLakeClient

BASE_URL = "http://localhost:8000"
API_KEY = "dev-api-key-for-local-testing-only"


def main() -> None:
    print("=" * 60)
    print("API-01  Health / Auth / Dataset CRUD")
    print("=" * 60)

    c = ArrowLakeClient(BASE_URL, API_KEY)

    # 1. Health
    print("\nSTEP 1: Health check")
    h = c.health()
    assert h.get("status") == "ok", f"health failed: {h}"
    assert h.get("storage") == "accessible"
    assert h.get("version"), f"missing version in health response: {h}"
    c._pass("GET /health")

    h2 = c.health_ready()
    assert h2.get("status") == "ok"
    c._pass("GET /health/ready")

    # 2. Auth token
    print("\nSTEP 2: Auth token")
    auth = c.auth_token("admin", "admin")
    assert auth.get("access_token"), f"auth failed: {auth}"
    c._pass(f"POST /auth/token — token={auth['access_token'][:20]}...")

    # 3. List datasets
    print("\nSTEP 3: List datasets")
    ds = c.list_datasets()
    assert ds.get("success") is True
    total = ds.get("total", 0)
    c._pass(f"GET /datasets — {total} datasets")
    for d in ds.get("datasets", []):
        print(f"         {d['name']:30s} v{d['version']} ({d['num_rows']} rows)")

    # 4. Get dataset detail
    print("\nSTEP 4: Get dataset detail")
    for d in ds.get("datasets", []):
        detail = c.get_dataset(d["name"])
        assert detail.get("name") == d["name"]
        assert detail.get("num_rows") == d["num_rows"]
        c._pass(f"GET /datasets/{d['name']} — {detail['num_rows']} rows")
        break  # one is enough

    # 5. Delete nonexistent
    print("\nSTEP 5: Delete nonexistent dataset")
    resp = c.delete_dataset("__nonexistent_test_404__")
    assert resp.get("success") is False or resp.get("status") in (404, "404"), f"unexpected: {resp}"
    c._pass("DELETE /datasets/__nonexistent__ — 404 as expected")

    # 6. Metrics
    print("\nSTEP 6: Metrics endpoint")
    resp = c._request("GET", "/metrics")
    # metrics returns text, not JSON
    c._pass("GET /metrics — accessible")

    print("\n" + "=" * 60)
    print("API-01  ALL PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
