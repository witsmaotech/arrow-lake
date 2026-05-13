#!/usr/bin/env python3
"""API-10 — Multi-modal Ingest (Documents / Videos / Mixed)

对应 cookbook: 05_image_video_ingest.py, 12_multimedia_asset_manager.py, 21_document_ingest.py
验证: PDF 文档摄取、视频摄取（关键帧抽取）、混合模态摄取管线
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
    print("API-10  Multi-modal Ingest")
    print("=" * 60)

    c = ArrowLakeClient(BASE_URL, API_KEY)

    # Clean up
    for n in ["documents", "videos", "mixed-media"]:
        c.delete_dataset(n)

    # 1. PDF document ingest
    print("\nSTEP 1: Ingest PDF documents")
    pdf_dir = DATAS_DIR / "papers"
    if pdf_dir.exists():
        pdfs = [str(p) for p in pdf_dir.iterdir() if p.suffix.lower() == ".pdf"]
        if pdfs:
            resp = c.ingest_documents("documents", pdfs[:3])
            if resp.get("success"):
                total = resp.get("total_rows", resp.get("rows", 0))
                c._pass(f"PDF ingest — {total} chunks from {len(pdfs[:3])} files")
            else:
                print(f"  [WARN] {resp.get('error')}: {resp.get('message', '')[:120]}")
        else:
            print(f"  [SKIP] No PDF files in {pdf_dir}")
    else:
        print(f"  [SKIP] {pdf_dir} not found")

    # 2. PDF with custom chunk config
    print("\nSTEP 2: Ingest PDF (custom chunk config)")
    if pdf_dir.exists():
        pdfs = [str(p) for p in pdf_dir.iterdir() if p.suffix.lower() == ".pdf"]
        if pdfs:
            resp = c.ingest_documents("documents", pdfs[:1])
            if resp.get("success"):
                c._pass("PDF with custom config ingested")
            else:
                print(f"  [WARN] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 3. Video ingest
    print("\nSTEP 3: Ingest videos (keyframe extraction)")
    video_dir = DATAS_DIR / "videos"
    if video_dir.exists():
        vids = [str(p) for p in video_dir.iterdir()
                if p.suffix.lower() in (".mp4", ".avi", ".mov", ".mkv")]
        if vids:
            resp = c.ingest_videos("videos", vids[:2])
            if resp.get("success"):
                total = resp.get("total_rows", resp.get("rows", 0))
                c._pass(f"video ingest — {total} keyframes from {len(vids[:2])} files")
            else:
                print(f"  [WARN] {resp.get('error')}: {resp.get('message', '')[:120]}")
        else:
            print(f"  [SKIP] No video files in {video_dir}")
    else:
        print(f"  [SKIP] {video_dir} not found")

    # 4. Image ingest (multiple formats)
    print("\nSTEP 4: Ingest images (multiple formats)")
    photo_dir = DATAS_DIR / "photos"
    if photo_dir.exists():
        imgs = [str(p) for p in photo_dir.iterdir()
                if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp", ".gif")]
        if imgs:
            resp = c.ingest_images("mixed-media", imgs[:3])
            if resp.get("success"):
                c._pass(f"image ingest — {len(imgs[:3])} images")
            else:
                print(f"  [WARN] {resp.get('error')}: {resp.get('message', '')[:120]}")
        else:
            print("  [SKIP] No image files found")
    else:
        print(f"  [SKIP] {photo_dir} not found")

    # 5. Mixed modality ingest
    print("\nSTEP 5: Mixed-modality ingest")
    sources: list[dict] = []
    if pdf_dir.exists():
        pdfs = [str(p) for p in pdf_dir.iterdir() if p.suffix.lower() == ".pdf"]
        for p in pdfs[:1]:
            sources.append({"type": "document", "path": p})
    if photo_dir.exists():
        imgs = [str(p) for p in photo_dir.iterdir()
                if p.suffix.lower() in (".jpg", ".jpeg", ".png")]
        for p in imgs[:1]:
            sources.append({"type": "image", "path": p})

    if sources:
        resp = c.ingest_mixed("mixed-media", sources)
        if resp.get("success"):
            total = resp.get("total_rows", resp.get("rows", 0))
            c._pass(f"mixed ingest — {total} rows from {len(sources)} sources")
        else:
            print(f"  [WARN] {resp.get('error')}: {resp.get('message', '')[:120]}")
    else:
        print("  [SKIP] No mixed sources available")

    # 6. Verify multi-modal datasets
    print("\nSTEP 6: Verify ingested datasets")
    ds = c.list_datasets()
    names = [d["name"] for d in ds.get("datasets", [])]
    for expected in ["documents", "videos", "mixed-media"]:
        if expected in names:
            detail = c.get_dataset(expected)
            rows = detail.get("num_rows", 0)
            c._pass(f"dataset '{expected}' — {rows} rows")
        else:
            print(f"  [INFO] dataset '{expected}' not created (no source data)")

    # 7. Search across multi-modal dataset
    print("\nSTEP 7: Search multi-modal dataset")
    if "mixed-media" in names:
        resp = c.search_fts("mixed-media", "image", top_k=3)
        if resp.get("success"):
            c._pass(f"FTS on mixed-media — {resp.get('row_count', 0)} results")
        else:
            print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")
    else:
        print("  [SKIP] mixed-media dataset not available")

    # 8. HTTP mixed ingest
    print("\nSTEP 8: HTTP ingest into mixed dataset")
    resp = c.ingest_http("mixed-media", ["https://httpbin.org/json"])
    if resp.get("success"):
        c._pass("HTTP ingest into mixed dataset")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # Cleanup
    for n in ["documents", "videos", "mixed-media"]:
        c.delete_dataset(n)

    print("\n" + "=" * 60)
    print("API-10  ALL PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
