#!/usr/bin/env python3
"""API-35 — Doc-Type Categories: the runtime category dictionary (v1.10.0)

Business scenario: an admin extends the doc_type taxonomy at runtime so a new
category (e.g. ``security``) becomes immediately usable both as an ingest
``doc_type`` and as a template ``category`` — no rebuild, no restart.

Capabilities (v1.10.0 M5):
  * ``GET /api/v1/kg/doc-types`` (VIEWER) — the gallery read path used by UI
    dropdowns; returns the seed 11 categories (dynamic via the facade).
  * ``GET /api/v1/admin/doc-type-categories`` (ADMIN) — list seed + custom.
  * ``POST /api/v1/admin/doc-type-categories`` (ADMIN, 201) — add a category.
    * 409 on duplicate (``CATEGORY_DUPLICATE``).
    * 422 on a bad name pattern ``^[a-z][a-z0-9_]{0,63}$`` (``CATEGORY_INVALID``).
  * ``DELETE /api/v1/admin/doc-type-categories/{name}`` (ADMIN).

Models: ``arrow_lake/api/routers/doc_type_categories.py:CategoryCreate``
(name / desc_zh / desc_en / aliases).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import ArrowLakeClient

BASE_URL = os.environ.get("ARROW_LAKE_BASE_URL", "http://localhost:8000")
API_KEY = os.environ.get("ARROW_LAKE_API_KEY", "dev-api-key-for-local-testing-only")
ADMIN = "/api/v1/admin/doc-type-categories"

CUSTOM_NAME = "security"


def main() -> None:
    print("=" * 64)
    print("API-35  Doc-Type Categories (runtime category dictionary, v1.10.0)")
    print("=" * 64)

    c = ArrowLakeClient(BASE_URL, API_KEY)

    # --- Step 1: read the doc-type gallery (VIEWER path, used by UI) ---
    print("\n# --- Step 1: GET /api/v1/kg/doc-types (gallery, VIEWER) ---")
    resp = c._request("GET", "/api/v1/kg/doc-types")
    if resp.get("success"):
        data = resp.get("data") or {}
        cats = data.get("categories") or data.get("doc_types") or []
        print(f"  [INFO] seed categories ({len(cats)}):")
        for dt in cats[:12]:
            # items are dicts {name, ...} or plain strings depending on version
            name = dt.get("name") if isinstance(dt, dict) else dt
            print(f"         - {name}")
    else:
        print(f"  [INFO] {resp.get('detail') or resp.get('error')}")

    # --- Step 2: admin list (seed + custom, with descriptions + aliases) ---
    print("\n# --- Step 2: GET /admin/doc-type-categories (ADMIN) ---")
    resp = c._request("GET", ADMIN)
    if resp.get("success"):
        print(f"  [INFO] {resp.get('count')} categories total")
        for item in (resp.get("data") or [])[:5]:
            print(f"         {item.get('name')} (source={item.get('source')})")
    else:
        print(f"  [INFO] {resp.get('detail') or resp.get('error')}")

    # --- Step 3: add a custom category (201 Created) ---
    print(f"\n# --- Step 3: POST add custom category '{CUSTOM_NAME}' ---")
    body = {
        "name": CUSTOM_NAME,
        "desc_zh": "信息安全领域文档(漏洞、威胁、资产、控制)",
        "desc_en": "Information-security documents (vulns, threats, assets, controls)",
        "aliases": ["infosec", "cybersecurity", "信息安全"],
    }
    resp = c._request("POST", ADMIN, body)
    print(f"  -> success={resp.get('success')} status={resp.get('status')}")
    if resp.get("status") == 201:
        c._pass(f"added '{CUSTOM_NAME}'")
    elif resp.get("status") == 409:
        print(f"  [INFO] already exists (409 CATEGORY_DUPLICATE) — fine for re-runs")

    # --- Step 4: duplicate add -> 409 conflict ---
    print("\n# --- Step 4: duplicate add -> 409 CATEGORY_DUPLICATE ---")
    resp = c._request("POST", ADMIN, body)
    print(f"  -> status={resp.get('status')} detail={resp.get('detail')}")

    # --- Step 5: invalid name -> 422 CATEGORY_INVALID ---
    print("\n# --- Step 5: invalid name 'Bad-Name!' -> 422 ---")
    resp = c._request("POST", ADMIN, {"name": "Bad-Name!", "desc_en": "reject me"})
    print(f"  -> status={resp.get('status')} detail={resp.get('detail')}")
    # name must match ^[a-z][a-z0-9_]{0,63}$

    # --- Step 6: the new category is now usable as a template doc_type ---
    print(f"\n# --- Step 6: '{CUSTOM_NAME}' is usable as a template doc_type ---")
    print("  -> POST /admin/extraction-templates with doc_type='security' validates")
    print("     against this dictionary (Layer-2 routing: template.category == doc_type)")
    print("  -> ingest with doc_type='security' routes to that template family")

    # --- Step 7: cleanup — delete the custom category ---
    print(f"\n# --- Step 7: DELETE /admin/doc-type-categories/{CUSTOM_NAME} ---")
    resp = c._request("DELETE", f"{ADMIN}/{CUSTOM_NAME}")
    print(f"  -> success={resp.get('success')} data={resp.get('data')}")

    print("\n" + "=" * 64)
    print("API-35  Doc-Type Categories — DONE")
    print("=" * 64)


if __name__ == "__main__":
    main()
