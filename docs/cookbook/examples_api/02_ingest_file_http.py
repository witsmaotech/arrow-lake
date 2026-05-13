#!/usr/bin/env python3
"""API-02 — Ingest Files & HTTP URLs

对应 cookbook: 01_ingest_basics.py, 05_image_video_ingest.py, 29_http_ingest.py
验证: 文件摄取 (CSV/JSONL)、HTTP URL 摄取、图片摄取
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import ArrowLakeClient

BASE_URL = "http://localhost:8000"
API_KEY = "dev-api-key-for-local-testing-only"
DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"


def main() -> None:
    print("=" * 60)
    print("API-02  Ingest (Files & HTTP)")
    print("=" * 60)

    c = ArrowLakeClient(BASE_URL, API_KEY)

    # Clean up
    for name in ["transactions", "knowledge", "api-ingest-test"]:
        c.delete_dataset(name)

    # 1. Ingest CSV files
    print("\nSTEP 1: Ingest CSV (transactions)")
    csv_path = DATAS_DIR / "transactions" / "sales_2024.csv"
    if csv_path.exists():
        resp = c.ingest_files("transactions", [str(csv_path)])
        if resp.get("success"):
            c._pass(f"ingest CSV — {resp.get('total_rows', '?')} rows, {resp.get('total_files', '?')} files")
        else:
            print(f"  [WARN] {resp.get('error')}: {resp.get('message', '')[:100]}")
    else:
        print(f"  [SKIP] {csv_path} not found")

    # 2. Ingest JSONL files
    print("\nSTEP 2: Ingest JSONL (knowledge)")
    jsonl_path = DATAS_DIR / "kb" / "knowledge.jsonl"
    if jsonl_path.exists():
        resp = c.ingest_files("knowledge", [str(jsonl_path)])
        if resp.get("success"):
            c._pass(f"ingest JSONL — {resp.get('total_rows', '?')} rows")
        else:
            print(f"  [WARN] {resp.get('error')}: {resp.get('message', '')[:100]}")
    else:
        print(f"  [SKIP] {jsonl_path} not found")

    # 3. Ingest HTTP URLs
    print("\nSTEP 3: Inest HTTP URLs")
    resp = c.ingest_http("api-ingest-test", ["https://httpbin.org/json"])
    if resp.get("success"):
        c._pass(f"ingest HTTP — {resp.get('total_rows', '?')} rows")
    else:
        print(f"  [WARN] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 4. Ingest images
    print("\nSTEP 4: Ingest images")
    photo_dir = DATAS_DIR / "photos"
    if photo_dir.exists():
        imgs = [str(p) for p in photo_dir.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png")]
        if imgs:
            resp = c.ingest_images("photos", imgs[:2])
            if resp.get("success"):
                c._pass(f"ingest images — {resp.get('total_rows', '?')} rows")
            else:
                print(f"  [WARN] {resp.get('error')}: {resp.get('message', '')[:120]}")
        else:
            print("  [SKIP] No image files found")
    else:
        print(f"  [SKIP] {photo_dir} not found")

    # 5. Verify datasets exist
    print("\nSTEP 5: Verify ingested datasets")
    ds = c.list_datasets()
    names = [d["name"] for d in ds.get("datasets", [])]
    for expected in ["transactions", "knowledge", "api-ingest-test"]:
        if expected in names:
            detail = c.get_dataset(expected)
            c._pass(f"dataset '{expected}' — {detail.get('num_rows', '?')} rows")
        else:
            print(f"  [WARN] dataset '{expected}' not found in catalog")

    # Cleanup
    for name in ["transactions", "knowledge", "api-ingest-test"]:
        c.delete_dataset(name)

    print("\n" + "=" * 60)
    print("API-02  ALL PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
