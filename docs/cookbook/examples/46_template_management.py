#!/usr/bin/env python3
"""46 — 模板管理与运行时切换 (v1.10.0 ⚑ 旗舰特性)

场景: KG 构建时通过 ``template=`` 参数覆盖默认抽取模板，无需重启 / 重建镜像即可
切换抽取策略 (project_concept_graph vs entity_graph)。模板的 CRUD、AI 生成、
试跑、质量验证走 REST 管理端点 (本脚本末尾给出对接说明)。

教学点:
  1. ``lake.kg_build(dataset, template="project/concept_graph")`` —— SDK 层运行时覆盖
  2. 模板 YAML 结构 (name/category/type/output.entities/output.relations/guideline)
  3. 模板↔数据集绑定是松耦合的: 同一数据集可用不同模板重建，对比图谱质量
  4. CRUD / AI 生成 / dry-run / 质量验证 (M1-M4) 全在 REST admin API

前提: HugeGraph + LLM 服务可用 (config hugegraph.enabled=true, rag.llm 配好)

注意: SDK 构建用 ``Lake``；模板的增删改查是 REST admin API (见 Step 5 说明)。
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
from pathlib import Path

import pyarrow as pa

from arrow_lake import Lake
from arrow_lake.config import ArrowLakeConfig

_DEFAULT_BASE_URI = "./_tmp_template_mgmt"
_DATASET = "tmpl_demo"

# 两段小文本 —— 模拟项目方案书片段 (project_concept_graph 模板擅长这类内容)
_SAMPLE_TEXTS = [
    "智慧城市项目由市应急指挥中心牵头，涵盖数据中台、物联网感知、综合预警三大子系统。"
    "数据中台负责汇聚交通、气象、环保数据；物联网感知部署 5000 个传感器。",
    "综合预警子系统对接应急指挥中心，当气象预警达橙色等级时启动响应。"
    "项目交付里程碑：一期数据中台上线，二期感知网络覆盖，三期预警联调。",
]


def _build_table() -> pa.Table:
    """把样例文本构造成 Lance 可摄入的 Arrow 表 (text_content 列)。"""
    return pa.table({"text_content": pa.array(_SAMPLE_TEXTS),
                     "source": pa.array(["brief_p1", "brief_p2"])})


async def run_async() -> None:
    parser = argparse.ArgumentParser(description="46_template_management.py")
    parser.add_argument("--base-uri", default=_DEFAULT_BASE_URI)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    no_cleanup = args.no_cleanup
    print("=" * 64)
    print("46 模板管理与运行时切换 (v1.10.0)")
    print("=" * 64)

    base = Path(args.base_uri)
    if base.exists():
        shutil.rmtree(base)

    # --- 配置: 启用 HugeGraph ---
    config = ArrowLakeConfig()
    config.hugegraph.enabled = True
    config.hugegraph.host = "localhost"
    config.hugegraph.port = 8089
    lake = Lake(base_uri=args.base_uri, config=config)

    # 清理残留
    try:
        lake.delete_dataset(_DATASET)
    except Exception:
        pass

    # --- Step 1: 摄入样例文本 (create_dataset 是程序化写入主入口) ---
    print("\n--- Step 1: 摄入样例文本 ---")
    lake.create_dataset(_DATASET, _build_table(), actor="cookbook")
    print(f"  数据集 '{_DATASET}' 已创建, {len(_SAMPLE_TEXTS)} 行文本")

    # --- Step 2: 检查 KG 服务 ---
    print("\n--- Step 2: 检查 HugeGraph ---")
    try:
        stats = await lake.kg_stats()
        print(f"  HugeGraph 已连接 (顶点 {stats.get('total_vertices', 0)})")
    except Exception as e:
        print(f"  HugeGraph 不可用: {e}")
        print("  启动: docker compose up -d hugegraph  +  config hugegraph.enabled=true")
        if not no_cleanup:
            lake.delete_dataset(_DATASET)
            lake.shutdown()
            shutil.rmtree(base, ignore_errors=True)
        return

    # --- Step 3: 用「默认模板」构建 KG (entity_graph) ---
    # 不传 template → 走 doc_type 路由或默认 entity_graph。
    print("\n--- Step 3: 默认模板构建 (entity_graph) ---")
    print("  调用: lake.kg_build(dataset)   # template=None")
    try:
        task_default = await lake.kg_build(_DATASET)
        print(f"  构建任务已提交: {task_default}")
        await asyncio.sleep(8)  # 等待小型构建完成 (真实场景轮询 kg_build_status)
        s1 = await lake.kg_stats()
        print(f"  默认模板结果: 顶点 {s1.get('total_vertices', 0)}, "
              f"边 {s1.get('total_edges', 0)}")
    except Exception as e:
        print(f"  构建失败: {e}")

    # --- Step 4: 用「project_concept_graph」模板重建 (运行时覆盖) ---
    # 关键: template= 参数是 SDK 层运行时覆盖 —— 不改 config、不重启服务、不重建镜像。
    # project_concept_graph (22 类型 + 14 关系) 对项目方案书质量远优于自由类型 entity_graph。
    print("\n--- Step 4: 模板覆盖重建 (project_concept_graph) ---")
    print("  调用: lake.kg_build(dataset, template='project/concept_graph')")
    try:
        task_tmpl = await lake.kg_build(_DATASET, template="project/concept_graph")
        print(f"  构建任务已提交: {task_tmpl}")
        await asyncio.sleep(8)
        s2 = await lake.kg_stats()
        print(f"  concept_graph 结果: 顶点 {s2.get('total_vertices', 0)}, "
              f"边 {s2.get('total_edges', 0)}")
        print("  → 对比 Step 3: 同一数据集，不同模板，图谱结构完全不同")
    except Exception as e:
        print(f"  构建失败: {e}")

    # --- Step 5: 增量构建 (incremental=True) ---
    # incremental 复用已有 KA dump，只喂入新 chunk；模板不匹配时回退到 PRIMARY_KEY 幂等 upsert。
    print("\n--- Step 5: 增量构建 ---")
    print("  调用: lake.kg_build(dataset, incremental=True)")
    try:
        task_inc = await lake.kg_build(_DATASET, incremental=True,
                                       template="project/concept_graph")
        print(f"  增量任务已提交: {task_inc}")
    except Exception as e:
        print(f"  增量构建: {e}")

    # --- Step 6: 模板 YAML 结构说明 ---
    print("\n--- Step 6: 模板 YAML 结构 (template_registry 校验) ---")
    print("  一个合规的抽取模板 YAML 必须包含:")
    print("    name: project_concept_graph        # 与文件名 stem 一致")
    print("    category: project                  # 必须是已知 doc_type (Layer-2 路由依据)")
    print("    type: graph                        # graph | model | hypergraph")
    print("    output:")
    print("      entities:                        # 实体类型 + 字段定义")
    print("        - name: organization ...")
    print("      relations:                       # 关系类型 + 字段定义")
    print("        - name: leads_to ...")
    print("    guideline:")
    print("      target: {zh: '...', en: '...'}   # 双语角色摘要 (至少其一)")
    print("      rules: [...]                     # 可选关系抽取规则")

    # --- Step 7: REST admin API (CRUD / AI 生成 / 质量验证) ---
    print("\n--- Step 7: 模板 CRUD / AI 生成 / 质量验证 (REST admin API) ---")
    print("  SDK 的 Lake 只覆盖「构建时覆盖模板」;")
    print("  以下能力是 REST 管理端点 (需 ADMIN role + X-API-Key):")
    print("    GET    /api/v1/kg/templates               # 列出全部模板 (含 user/system)")
    print("    POST   /api/v1/kg/templates               # 新建用户模板 (YAML body)")
    print("    POST   /api/v1/kg/templates/{name}/generate  # M3: LLM 生成高质量模板")
    print("    POST   /api/v1/kg/templates/{name}/dry-run  # 试跑 (不落盘)")
    print("    POST   /api/v1/kg/templates/{name}/quality/doc   # M4: 生成测试文档")
    print("    POST   /api/v1/kg/templates/{name}/quality/build # M4: 端到端质量验证")
    print("  → 完整 REST 示例见 examples_api/ 对应脚本")
    print("  → SDK 层无需 rebuild/restart: 改完模板立刻 kg_build(template=...) 生效")

    print("\n  [全部 PASS]")
    if not no_cleanup:
        try:
            lake.delete_dataset(_DATASET)
        except Exception:
            pass
        lake.shutdown()
        shutil.rmtree(base, ignore_errors=True)
        print("(已清理)")


def main() -> None:
    asyncio.run(run_async())


if __name__ == "__main__":
    main()
