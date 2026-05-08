#!/usr/bin/env python3
"""05 — 多媒体摄取

演示图片和视频的摄取。

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
_DATASETS = ("photos", "videos")


def main() -> None:
    parser = argparse.ArgumentParser(description="05_image_video_ingest.py")
    parser.add_argument("--base-uri", default=_DEFAULT_BASE_URI)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    no_cleanup = args.no_cleanup
    print("=" * 60)
    print("05 多媒体摄取")
    print("=" * 60)

    base = Path(args.base_uri)
    if base.exists():
        shutil.rmtree(base)

    lake = Lake(base_uri=args.base_uri)

    # 清理 MinIO 后端可能残留的数据集
    for ds in _DATASETS:
        try:
            lake.delete_dataset(ds)
        except Exception:
            pass

    # --- STEP 1: 摄入图片 ---
    print("STEP 1: 摄入图片")
    photos = sorted((DATAS_DIR / "photos").glob("*.jpg"))
    if not photos:
        print("  跳过: 未找到图片文件")
    else:
        report = lake.ingest_images("photos", [str(p) for p in photos])
        print(f"  摄入: {report.total_rows} 张图片")
        print(f"  文件: {[p.name for p in photos]}")
        print("  [PASS]\n")

    # --- STEP 2: 摄入视频 ---
    print("STEP 2: 摄入视频")
    videos = sorted((DATAS_DIR / "videos").glob("*.mp4"))
    if not videos:
        print("  跳过: 未找到视频文件")
    else:
        report = lake.ingest_videos("videos", [str(v) for v in videos])
        print(f"  摄入: {report.total_rows} 个视频")
        print(f"  文件: {[v.name for v in videos]}")
        print("  [PASS]\n")

    # --- STEP 3: 查看数据集列表 ---
    print("STEP 3: 查看数据集")
    for name in lake.list_datasets():
        print(f"  {name}")
    print("  [PASS]\n")

    if not no_cleanup:
        for ds in _DATASETS:
            try:
                lake.delete_dataset(ds)
            except Exception:
                pass
        lake.shutdown()
        shutil.rmtree(base, ignore_errors=True)
        print("(已清理)")

    print("=" * 60)
    print("05 完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
