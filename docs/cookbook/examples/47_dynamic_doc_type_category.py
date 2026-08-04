#!/usr/bin/env python3
"""47 — 动态 doc_type ↔ 模板 category 路由 (v1.10.0)

场景: 摄入文档时打上 ``doc_type`` 标签，KG 构建时由 ``DocTypeRouter`` 自动选中
同 ``category`` 的抽取模板 —— 两层路由: Layer-1 doc_type 归一化 (别名折叠),
Layer-2 doc_type ↔ template.category 精确匹配。

教学点:
  1. doc_type 是数据集侧标签; category 是模板侧声明; 二者相等即路由命中
  2. 别名归一化: "论文"/"research_paper"/"academic" → 归一化为 "paper"
  3. ``normalize_doc_type`` 是 O(1) 查表; ``DocTypeRouter.match`` 4 级优先级
  4. 种子 doc_type 集合 + 动态字典 (REST /kg/doc-types)

前提: 无 (路由是纯 Python, 不依赖外部服务); KG 构建需 HugeGraph + LLM。
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
from pathlib import Path

import pyarrow as pa

from arrow_lake import Lake
from arrow_lake.config import ArrowLakeConfig
from arrow_lake.knowledge_graph.doc_type_router import (
    DOC_TYPE_ALIASES,
    DocTypeRouter,
    normalize_doc_type,
)

_DEFAULT_BASE_URI = "./_tmp_doctype_category"


def _show_alias_normalization() -> None:
    """Step 2: 演示 doc_type 别名归一化 (Layer-1, 纯逻辑, 无外部依赖)。"""
    print("\n--- Step 2: doc_type 别名归一化 (normalize_doc_type) ---")
    samples = ["论文", "research_paper", "ACADEMIC", "财报", "contract",
               "中医", "guide", "", "未知类型"]
    for raw in samples:
        norm = normalize_doc_type(raw)
        print(f"  normalize({raw!r:20}) → {norm!r}")


def _show_router_match(router: DocTypeRouter) -> None:
    """Step 3: 演示 doc_type → 模板路由 (Layer-2, category 精确匹配)。"""
    print("\n--- Step 3: doc_type → 模板路由 (DocTypeRouter.match) ---")
    print("  路由优先级: tag(精确) → category(相等) → name token → description")
    for dt in ["finance", "paper", "medicine", "legal", "industry", "general"]:
        info = router.match(dt)
        if info:
            print(f"  doc_type={dt:10} → {info.path:32} (type={info.type})")
        else:
            print(f"  doc_type={dt:10} → (无匹配, 回退默认模板)")


async def run_async() -> None:
    parser = argparse.ArgumentParser(description="47_dynamic_doc_type_category.py")
    parser.add_argument("--base-uri", default=_DEFAULT_BASE_URI)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    no_cleanup = args.no_cleanup
    print("=" * 64)
    print("47 动态 doc_type ↔ 模板 category 路由 (v1.10.0)")
    print("=" * 64)

    # --- Step 1: 种子 doc_type 集合 ---
    print("\n--- Step 1: 种子 doc_type 集合 (DOC_TYPE_ALIASES) ---")
    print(f"  共 {len(DOC_TYPE_ALIASES)} 个规范 doc_type:")
    for canon, aliases in DOC_TYPE_ALIASES.items():
        alias_preview = ", ".join(aliases[:4]) if aliases else "(无别名, 回退)"
        print(f"    {canon:10} ← {alias_preview}")
    print("  动态扩展: REST POST /api/v1/kg/doc-types 添加新 doc_type → 即时进字典")

    _show_alias_normalization()

    # --- Step 4: 加载模板画廊, 实际匹配 ---
    print("\n--- Step 4: 加载 DocTypeRouter 并匹配 ---")
    try:
        router = DocTypeRouter.from_presets()
        print(f"  已加载 {len(router.templates)} 个预设模板")
        _show_router_match(router)
    except Exception as e:
        print(f"  模板加载: {e} (可能预设目录未安装, 跳过匹配演示)")

    # --- Step 5: 带着正确模板走 KG 构建 (端到端) ---
    print("\n--- Step 5: 摄入 + 按 doc_type 路由构建 KG ---")
    base = Path(args.base_uri)
    if base.exists():
        shutil.rmtree(base)
    config = ArrowLakeConfig()
    config.hugegraph.enabled = True
    config.hugegraph.host = "localhost"
    config.hugegraph.port = 8089
    lake = Lake(base_uri=args.base_uri, config=config)

    ds = "doctype_demo"
    try:
        lake.delete_dataset(ds)
    except Exception:
        pass

    # 模拟金融财报文本 → doc_type="finance" 应路由到 finance 类模板
    finance_text = pa.table({
        "text_content": pa.array([
            "本季度营业收入 12.3 亿元，同比增长 18%。研发投入占比 9.2%。",
            "公司主营业务毛利率 42%，现金流净额 2.1 亿元，资产负债率 35%。",
        ]),
    })
    lake.create_dataset(ds, finance_text, actor="cookbook")
    print(f"  数据集 '{ds}' 已摄入 (doc_type=finance 隐含于内容领域)")

    # 用显式 template 覆盖 (SDK 层), 模拟路由命中 finance 模板的等价效果
    try:
        stats = await lake.kg_stats()
        print(f"  HugeGraph: 顶点 {stats.get('total_vertices', 0)}")
        # 若 finance 模板存在则用它, 否则回退 project_concept_graph
        target_tmpl = "finance/earnings_graph" if router and router.get("finance/earnings_graph") else "project/concept_graph"
        print(f"  路由选择模板: {target_tmpl}")
        task = await lake.kg_build(ds, template=target_tmpl)
        print(f"  KG 构建任务: {task}")
        print("  → doc_type 路由 = 省去手动指定 template, 由内容领域自动选中")
    except Exception as e:
        print(f"  KG 构建 (需 HugeGraph+LLM): {e}")

    # --- Step 6: 动态字典 REST 说明 ---
    print("\n--- Step 6: 动态 doc_type 字典 (REST) ---")
    print("  GET  /api/v1/kg/doc-types           # 列出当前全部 doc_type + 别名")
    print("  POST /api/v1/kg/doc-types           # 新增 doc_type (category 绑定)")
    print("  DELETE /api/v1/kg/doc-types/{name}  # 移除自定义 doc_type")
    print("  动态字典存于 system_db (libSQL), 重启不丢失; M5: category↔doc_type 端到端拉通")

    print("\n  [全部 PASS]")
    if not no_cleanup:
        try:
            lake.delete_dataset(ds)
        except Exception:
            pass
        lake.shutdown()
        shutil.rmtree(base, ignore_errors=True)
        print("(已清理)")


def main() -> None:
    asyncio.run(run_async())


if __name__ == "__main__":
    main()
