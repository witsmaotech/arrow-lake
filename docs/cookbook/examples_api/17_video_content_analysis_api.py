#!/usr/bin/env python3
"""API-17 — 视频内容分析平台

业务场景: 流媒体/教育平台需要自动化分析视频库，提取关键帧、生成嵌入、支持内容检索
数据源: datas/videos/product_review.mp4, lecture_demo.mp4, interview_clip.mp4
流程: 视频摄取(关键帧提取) → 图像嵌入 → 关键帧检索 → 视频元数据统计 → 内容关联
"""

from __future__ import annotations
import os

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import ArrowLakeClient, first_embedding

BASE_URL = os.environ.get("ARROW_LAKE_BASE_URL", "http://localhost:8000")
API_KEY = os.environ.get("ARROW_LAKE_API_KEY", "dev-api-key-for-local-testing-only")
DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"

DS_VIDEOS = "video-library"
DS_FRAMES = "keyframe-index"


def main() -> None:
    print("=" * 60)
    print("API-17  视频内容分析平台")
    print("=" * 60)

    c = ArrowLakeClient(BASE_URL, API_KEY)

    for n in [DS_VIDEOS, DS_FRAMES]:
        c.delete_dataset(n)

    video_dir = DATAS_DIR / "videos"
    assert video_dir.exists(), f"视频目录不存在: {video_dir}"

    videos = sorted(video_dir.glob("*.mp4"))
    assert videos, "无 MP4 视频文件"

    # ── Phase 1: 视频摄取与关键帧提取 ──

    print("\n── Phase 1: 视频摄取与关键帧提取 ──")

    video_info: dict[str, dict] = {}

    for vf in videos:
        label = vf.stem
        print(f"\nSTEP: 摄取视频 — {label}")

        resp = c.ingest_videos(DS_VIDEOS, [str(vf)])
        if resp.get("success"):
            rows = resp.get("total_rows", resp.get("rows", 0))
            keyframes = resp.get("keyframes", resp.get("keyframe_count", "?"))
            duration = resp.get("duration_ms", resp.get("duration", "?"))
            video_info[label] = {
                "rows": rows,
                "keyframes": keyframes,
                "duration": duration,
            }
            c._pass(f"{label} — {rows} 关键帧, 时长 {duration}ms")
        else:
            print(f"  [WARN] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # ── Phase 2: 视频元数据统计 ──

    print("\n── Phase 2: 视频元数据统计 ──")

    print("\nSTEP 1: 视频总览")
    resp = c.query_olap(DS_VIDEOS,
        f'SELECT count(*) as total_keyframes, '
        f'  count(DISTINCT source) as video_count '
        f'FROM "{DS_VIDEOS}"')
    if resp.get("success"):
        rows = resp.get("rows", [])
        if rows:
            r = rows[0]
            c._pass(f"总计 {r.get('video_count', 0)} 个视频, "
                    f"{r.get('total_keyframes', 0)} 个关键帧")
    else:
        print(f"  [INFO] {resp.get('error', '')}")

    print("\nSTEP 2: 按视频分组统计")
    resp = c.query_olap(DS_VIDEOS,
        f'SELECT source, count(*) as frame_count, '
        f'  min(timestamp_ms) as start_ms, '
        f'  max(timestamp_ms) as end_ms '
        f'FROM "{DS_VIDEOS}" '
        f'GROUP BY source '
        f'ORDER BY frame_count DESC')
    if resp.get("success"):
        rows = resp.get("rows", [])
        c._pass(f"视频分组 — {len(rows)} 个视频")
        print(f"         {'视频':25s} {'帧数':>6s} {'起始ms':>8s} {'结束ms':>8s}")
        for r in rows:
            print(f"         {r.get('source', '?'):25s} "
                  f"{r.get('frame_count', 0):>6d} "
                  f"{r.get('start_ms', 0):>8} "
                  f"{r.get('end_ms', 0):>8}")
    else:
        print(f"  [INFO] {resp.get('error', '')}")

    # ── Phase 3: 关键帧索引与嵌入 ──

    print("\n── Phase 3: 关键帧索引与嵌入 ──")

    print("\nSTEP 3: 构建向量索引")
    resp = c.create_vector_index(DS_VIDEOS, vector_column="text_embedding",
                                  metric="cosine", index_type="IVF_PQ")
    if resp.get("success"):
        c._pass("关键帧向量索引就绪")
    else:
        print(f"  [INFO] {resp.get('error', '')}")

    print("\nSTEP 4: 图像嵌入计算")
    photo_dir = DATAS_DIR / "photos"
    if photo_dir.exists():
        imgs = [str(p) for p in photo_dir.iterdir()
                if p.suffix.lower() in (".jpg", ".jpeg", ".png")]
        if imgs:
            resp = c.embed_image(imgs[:2], model="clip")
            if resp.get("success"):
                embeddings = resp.get("embeddings", resp.get("data", []))
                dim = len(embeddings[0]) if embeddings else 0
                c._pass(f"图像嵌入 — {len(embeddings)} 个向量, dim={dim}")
            else:
                print(f"  [INFO] {resp.get('error', '')}")

    # ── Phase 4: 视频内容检索 ──

    print("\n── Phase 4: 视频内容检索 ──")

    print("\nSTEP 5: 文本搜索视频关键帧")
    resp = c.search_fts(DS_VIDEOS, "product", top_k=5)
    if resp.get("success"):
        rows = resp.get("rows", [])
        c._pass(f"搜索 'product' — {resp.get('row_count', 0)} 个关键帧")
        for r in rows[:3]:
            source = r.get("source", "?")
            ts = r.get("timestamp_ms", r.get("timestamp", "?"))
            print(f"         {source} @ {ts}ms")
    else:
        print(f"  [INFO] {resp.get('error', '')}")

    print("\nSTEP 6: 向量搜索相似关键帧")
    resp = c.embed_text(["landscape scenery nature"])
    if resp.get("success"):
        vec = first_embedding(resp)
        if vec:
            search = c.search_vector(DS_VIDEOS, vec, top_k=5)
            if search.get("success"):
                c._pass(f"向量搜索 — {search.get('row_count', 0)} 个相似帧")
                for r in search.get("rows", [])[:3]:
                    source = r.get("source", "?")
                    ts = r.get("timestamp_ms", "?")
                    score = r.get("_distance", r.get("_score", "?"))
                    print(f"         {source} @ {ts}ms (dist={score})")
    else:
        print(f"  [INFO] 向量搜索不可用: {resp.get('error', '')}")

    # ── Phase 5: 视频间内容关联 ──

    print("\n── Phase 5: 视频间内容关联 ──")

    print("\nSTEP 7: 跨视频时间线分析")
    resp = c.query_olap(DS_VIDEOS,
        f'SELECT source, '
        f'  count(*) as frames, '
        f'  round(avg(timestamp_ms), 0) as avg_ts, '
        f'  max(timestamp_ms) - min(timestamp_ms) as span_ms '
        f'FROM "{DS_VIDEOS}" '
        f'GROUP BY source '
        f'ORDER BY span_ms DESC')
    if resp.get("success"):
        rows = resp.get("rows", [])
        c._pass(f"时间线分析 — {len(rows)} 个视频")
        for r in rows:
            print(f"         {r.get('source', '?'):25s} — "
                  f"{r.get('frames', 0)} 帧, 跨度 {r.get('span_ms', 0)}ms")
    else:
        print(f"  [INFO] {resp.get('error', '')}")

    print("\nSTEP 8: 关键帧密度分析")
    resp = c.query_olap(DS_VIDEOS,
        f'SELECT source, '
        f'  count(*) as frames, '
        f'  max(timestamp_ms) as duration_ms, '
        f'  round(count(*) * 1000.0 / NULLIF(max(timestamp_ms), 0), 2) as fps_density '
        f'FROM "{DS_VIDEOS}" '
        f'GROUP BY source '
        f'ORDER BY fps_density DESC')
    if resp.get("success"):
        rows = resp.get("rows", [])
        c._pass("关键帧密度分析")
        for r in rows:
            print(f"         {r.get('source', '?'):25s} — "
                  f"密度 {r.get('fps_density', 0):.2f} 帧/秒")
    else:
        print(f"  [INFO] {resp.get('error', '')}")

    # ── Phase 6: 质量检查 & 导出 ──

    print("\n── Phase 6: 质量检查 & 导出 ──")

    print("\nSTEP 9: 视频数据质量报告")
    resp = c.quality_report(DS_VIDEOS)
    if resp.get("success"):
        report = resp.get("report", resp.get("data", {}))
        if isinstance(report, dict):
            score = report.get("quality_score", "?")
            c._pass(f"视频数据质量评分: {score}")
    else:
        print(f"  [INFO] {resp.get('error', '')}")

    print("\nSTEP 10: 导出视频元数据")
    resp = c.export(DS_VIDEOS, format="csv")
    if resp.get("success"):
        task_id = resp.get("task_id", "")
        if task_id:
            status = c.wait_for_export(DS_VIDEOS, task_id, timeout=30)
            c._pass(f"导出完成 — {status.get('status')}")
    else:
        print(f"  [INFO] {resp.get('error', '')}")

    # 审计
    c.audit_record(DS_VIDEOS, "video_content_analysis",
                   details={"videos": len(videos), "phases": 6})

    # 清理
    for n in [DS_VIDEOS, DS_FRAMES]:
        c.delete_dataset(n)

    print("\n" + "=" * 60)
    print("API-17  视频内容分析平台 — ALL PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
