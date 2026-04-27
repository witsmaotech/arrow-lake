#!/usr/bin/env python3
"""12 — 多媒体资产管理

场景: 摄入并管理图片/视频资产。

数据文件: datas/photos/*.jpg, datas/videos/*.mp4
"""

from __future__ import annotations

import argparse

import shutil
import sys
from pathlib import Path

from arrow_lake import Lake

DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"
_DEFAULT_BASE_URI = "./_tmp_multimedia"


def main() -> None:
    parser = argparse.ArgumentParser(description="12_multimedia_asset_manager.py")
    parser.add_argument("--base-uri", default=_DEFAULT_BASE_URI)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    no_cleanup = args.no_cleanup
    print("=" * 60)
    print("12 多媒体资产管理")
    print("=" * 60)

    base = Path(args.base_uri)
    if base.exists():
        shutil.rmtree(base)

    lake = Lake(base_uri=args.base_uri)

    # STEP 1
    print("STEP 1: 摄入图片资产")
    photos = sorted((DATAS_DIR / "photos").glob("*.jpg"))
    if photos:
        r = lake.ingest_images("photos", [str(p) for p in photos])
        print(f"  摄入 {r.total_rows} 张图片")
        ds = lake.open_dataset("photos")
        schema = ds.schema
        print(f"  列: {[f'{f.name} ({f.type})' for f in schema]}")
    else:
        print("  跳过: 无图片文件")

    # STEP 2
    print("\nSTEP 2: 摄入视频资产")
    videos = sorted((DATAS_DIR / "videos").glob("*.mp4"))
    if videos:
        r = lake.ingest_videos("videos", [str(v) for v in videos])
        print(f"  摄入 {r.total_rows} 个视频")
        ds = lake.open_dataset("videos")
        schema = ds.schema
        print(f"  列: {[f'{f.name} ({f.type})' for f in schema]}")
    else:
        print("  跳过: 无视频文件")

    # STEP 3
    print("\nSTEP 3: 数据集总览")
    for name in lake.list_datasets():
        ds = lake.open_dataset(name)
        print(f"  {name}: {ds.count_rows()} 行, {len(ds.schema)} 列")

    print("\n  [全部 PASS]")
    if not no_cleanup:
        lake.shutdown()
        shutil.rmtree(base, ignore_errors=True)
        print("(已清理)")


if __name__ == "__main__":
    main()
