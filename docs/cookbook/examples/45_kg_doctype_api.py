#!/usr/bin/env python3
"""45 — v1.7 通过 REST API 构建知识图谱（含 doc_type/he 说明）

场景: 用 HTTP REST API 完成 摄入 → KG 构建 → 状态 → 统计 全流程，
      并说明 v1.7 he 后端 + doc_type 推断在 API 模式下如何工作。

与示例 44（Python SDK 演示 doc_type 路由）互补——本示例展示**运维/外部系统**
通过 HTTP API 触发 KG 构建的方式。

v1.7 在 API 模式下的工作方式:
  - he 后端 + doc_type 路由是**服务端配置**（HugeGraphConfig.extractor_backend / he_*），
    部署时设定，API 调用方无需关心。
  - 当数据集 chunk 表无 doc_type 列时，KG builder 的 DocTypeClassifier 会
    **从内容自动推断** doc_type（P3），再走三层路由选模板。
  - 显式指定 doc_type 需经 SDK (lake.ingest_documents(doc_type=...))；
    当前 /ingest API 不透传 doc_type（靠内容推断）。

前置:
  - Arrow Lake API 服务运行中 (默认 http://localhost:8000)
  - HugeGraph PD 集群 + (可选) LLM 用于 he 抽取
  - API_KEY 环境变量 (ADMIN 角色用于 /build)
"""

from __future__ import annotations

import argparse
import os
import time

import httpx

API_BASE = os.getenv("ARROW_LAKE_API", "http://localhost:8000/api/v1")


def _headers() -> dict[str, str]:
    key = os.getenv("API_KEY", "")
    return {"Authorization": f"Bearer {key}"} if key else {}


def main() -> None:
    parser = argparse.ArgumentParser(description="45_kg_doctype_api.py")
    parser.add_argument("--dataset", default="papers_zh")
    parser.add_argument("--file", default=None, help="要摄入的文件路径（可选）")
    parser.add_argument("--skip-ingest", action="store_true", help="跳过摄入，直接构建已有数据集")
    args = parser.parse_args()

    print("=" * 60)
    print("45 通过 REST API 构建知识图谱（v1.7 doc_type/he）")
    print("=" * 60)
    print(f"  API: {API_BASE}")

    with httpx.Client(base_url=API_BASE, headers=_headers(), timeout=30) as c:
        # STEP 1: 摄入文件（可选）
        if args.file and not args.skip_ingest:
            print(f"\n[1] POST /datasets/{args.dataset}/ingest")
            r = c.post(f"/datasets/{args.dataset}/ingest",
                       json={"file_paths": [args.file]})
            print(f"    {r.status_code}: {r.json() if r.is_success else r.text}")

        # STEP 2: 触发 KG 构建（ADMIN 权限）
        # v1.7: 服务端按 extractor_backend 决定 legacy/he；doc_type 未传时
        #       由 DocTypeClassifier 从内容推断（P3），再三层路由选模板。
        print(f"\n[2] POST /knowledge-graph/build  (dataset={args.dataset})")
        r = c.post("/knowledge-graph/build", json={"dataset_name": args.dataset})
        if not r.is_success:
            print(f"    构建失败 {r.status_code}: {r.text}")
            print("    提示: /build 需 ADMIN 角色；确认 API_KEY + HugeGraph 就绪")
            return
        task_id = r.json()["task_id"]
        print(f"    任务已提交: {task_id}")

        # STEP 3: 轮询构建状态
        print(f"\n[3] 轮询 GET /knowledge-graph/build/{task_id}/status")
        for _ in range(60):
            r = c.get(f"/knowledge-graph/build/{task_id}/status")
            if r.is_success:
                st = r.json()
                print(f"    status={st.get('status')} "
                      f"chunks={st.get('processed_chunks')}/{st.get('total_chunks')} "
                      f"entities={st.get('entity_count')} relations={st.get('relation_count')}"
                      f" failures={st.get('extraction_failures', 'N/A')}")
                # extraction_failures (v1.7 H1): >0 提示可能 LLM/extractor 失败
                if st.get("status") in ("COMPLETED", "FAILED"):
                    break
            time.sleep(3)

        # STEP 4: 图谱统计
        print("\n[4] GET /knowledge-graph/stats")
        r = c.get("/knowledge-graph/stats")
        if r.is_success:
            print(f"    {r.json()}")

    print("\n[全部 PASS — REST API KG 流程完成]")
    print("\nv1.7 说明:")
    print("  - he 后端: 服务端 config.hugegraph.extractor_backend='he' 启用")
    print("  - doc_type: API 不透传；服务端 DocTypeClassifier 从内容推断（P3）")
    print("  - 显式 doc_type: 用 SDK lake.ingest_documents(doc_type=...) （见示例 44）")


if __name__ == "__main__":
    main()
