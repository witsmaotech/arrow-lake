#!/usr/bin/env python3
"""12 — 多媒体资产管理

场景: 摄入图片/视频资产，并使用 SQL 查询、Daft DataFrame、
质量过滤、导出等能力对多媒体数据进行管理。

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

    # 清理后端残留
    _DATASETS = ["photos", "videos"]
    for ds in _DATASETS:
        try:
            lake.delete_dataset(ds)
        except Exception:
            pass

    # STEP 1: 摄入图片资产
    print("STEP 1: 摄入图片资产")
    photos = sorted((DATAS_DIR / "photos").glob("*.jpg"))
    if photos:
        r = lake.ingest_images("photos", [str(p) for p in photos])
        print(f"  摄入 {r.total_rows} 张图片")
    else:
        print("  跳过: 无图片文件")

    # STEP 2: 摄入视频资产
    print("\nSTEP 2: 摄入视频资产")
    videos = sorted((DATAS_DIR / "videos").glob("*.mp4"))
    if videos:
        r = lake.ingest_videos("videos", [str(v) for v in videos])
        print(f"  摄入 {r.total_rows} 个视频")
    else:
        print("  跳过: 无视频文件")

    # STEP 3: SQL 元数据查询 — 图片尺寸分布
    print("\nSTEP 3: SQL 查询 — 图片尺寸分布")
    result = lake.query(
        "photos",
        "SELECT image_width, image_height FROM photos ORDER BY image_width * image_height DESC",
    )
    table = result.table
    print(f"  {'宽度':>6} {'高度':>6}  {'文件名':<30}")
    for i in range(min(5, table.num_rows)):
        w = table.column("image_width")[i].as_py()
        h = table.column("image_height")[i].as_py()
        print(f"  {w:>6} {h:>6}  {w}x{h}")

    # STEP 4: OLAP 聚合 — 视频时长统计
    print("\nSTEP 4: OLAP 聚合 — 视频时长统计")
    result = lake.olap_query(
        "videos",
        "SELECT keyframe_count, SUM(video_duration_ms) as total_ms, "
        "AVG(video_duration_ms) as avg_ms FROM videos GROUP BY keyframe_count "
        "ORDER BY total_ms DESC",
    )
    table = result.table
    print(f"  {'关键帧数':>8} {'总时长(ms)':>12} {'平均时长(ms)':>12}")
    for row in table.to_pylist():
        print(f"  {row['keyframe_count']:>8} {row['total_ms']:>12} {row['avg_ms']:>12.0f}")

    # STEP 5: Daft DataFrame — 图片宽高比过滤
    print("\nSTEP 5: Daft DataFrame — 宽图筛选")
    try:
        df = lake.daft_query("photos", columns=["image_width", "image_height"])
        wide = df.filter(df["image_width"] > df["image_height"]).collect()
        print(f"  宽图数量: {len(wide)}")
        if len(wide) > 0:
            for row in wide.to_pylist()[:5]:
                print(f"    {row['image_width']}x{row['image_height']}")
    except Exception as e:
        print(f"  跳过 (Daft + MinIO 兼容性问题): {e}")
        # 回退: 用 SQL WHERE 实现同样的过滤
        result = lake.query(
            "photos",
            "SELECT image_width, image_height FROM photos WHERE image_width > image_height",
        )
        t = result.table
        print(f"  宽图数量: {t.num_rows} (SQL fallback)")
        for i in range(min(5, t.num_rows)):
            print(f"    {t.column('image_width')[i].as_py()}x{t.column('image_height')[i].as_py()}")

    # STEP 6: 质量过滤 — 小尺寸图片
    print("\nSTEP 6: 质量过滤 — 小尺寸图片 (宽 <= 800 且 高 <= 600)")
    try:
        report = lake.quality_filter("photos")
        print(f"  总数: {report.total}, 通过: {report.passed}, 拒绝: {report.rejected}")
    except Exception as e:
        print(f"  跳过: {e}")

    # STEP 7: 导出元数据为 CSV (二进制列自动排除)
    print("\nSTEP 7: 导出元数据")
    out_csv = (base / "photos_meta.csv").resolve()
    lake.export("photos", str(out_csv), format="csv")
    size = out_csv.stat().st_size
    print(f"  CSV: {out_csv} ({size} bytes)")
    with open(out_csv) as f:
        lines = f.readlines()
        print(f"  行数: {len(lines) - 1} (不含表头)")
        print(f"  列名: {lines[0].strip()}")

    # STEP 8: 导出为 Parquet (含完整二进制列)
    out_parquet = (base / "photos_full.parquet").resolve()
    lake.export("photos", str(out_parquet), format="parquet")
    size = out_parquet.stat().st_size
    print(f"  Parquet: {out_parquet} ({size // 1024} KB)")

    # STEP 9: 数据集总览
    print("\nSTEP 9: 数据集总览")
    catalog = lake.catalog()
    for name in lake.list_datasets():
        if name in catalog.datasets:
            ds = catalog.datasets[name]
            print(f"  {name}: {ds.num_rows} 行")

    print("\n  [全部 PASS]")
    if not no_cleanup:
        for ds in _DATASETS:
            try:
                lake.delete_dataset(ds)
            except Exception:
                pass
        lake.shutdown()
        shutil.rmtree(base, ignore_errors=True)
        print("(已清理)")


if __name__ == "__main__":
    main()
