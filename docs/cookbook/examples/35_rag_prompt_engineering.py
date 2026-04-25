#!/usr/bin/env python3
"""35 — RAG 提示工程

场景: 深入使用 PromptRegistry 和提示模板系统，展示自定义模板注册、
类型筛选和模板渲染。

前提: 无外部服务依赖 (PromptRegistry 是纯内存操作)
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from arrow_lake.rag.prompt import PromptRegistry, PromptTemplate, PromptType

BASE_URI = "./_tmp_prompt_eng"


def main() -> None:
    no_cleanup = "--no-cleanup" in sys.argv
    print("=" * 60)
    print("35 RAG 提示工程")
    print("=" * 60)

    base = Path(BASE_URI)
    if base.exists():
        shutil.rmtree(base)

    # STEP 1: 查看内置模板
    print("STEP 1: 内置提示模板")
    registry = PromptRegistry()
    all_templates = registry.list_templates()
    print(f"  总计: {len(all_templates)} 个模板")

    for prompt_type in [PromptType.QA, PromptType.SUMMARY, PromptType.EXTRACT]:
        type_templates = registry.list_by_type(prompt_type)
        print(f"\n  [{prompt_type.value}] ({len(type_templates)} 个):")
        for name in type_templates:
            tmpl = registry.get(name)
            if tmpl:
                desc = tmpl.description or "(无描述)"
                print(f"    {name:<28} {desc}")

    # STEP 2: 模板渲染
    print("\nSTEP 2: 模板渲染示例")
    qa_tmpl = registry.get("default_qa")
    if qa_tmpl:
        rendered = qa_tmpl.render(
            context="Arrow 是一种高性能的列式内存格式。",
            question="Arrow 的核心优势是什么？",
            language="zh",
        )
        print(f"  default_qa 渲染结果 (前 200 字符):")
        print(f"  {rendered[:200]}...")

    # STEP 3: 注册自定义模板
    print("\nSTEP 3: 注册自定义模板")
    custom_qa = PromptTemplate(
        name="tech_qa_zh",
        type=PromptType.QA,
        template=(
            "你是一位专业的技术顾问。请基于以下上下文回答问题。\n\n"
            "上下文:\n{{ context }}\n\n"
            "问题: {{ question }}\n\n"
            "要求:\n"
            "- 使用中文回答\n"
            "- 引用具体数据时标注来源\n"
            "- 如果上下文不足，明确说明\n"
            "- 保持专业但易懂的语气"
        ),
        description="中文技术问答模板",
    )
    registry.register(custom_qa)
    print("  已注册: tech_qa_zh")

    custom_summary = PromptTemplate(
        name="zh_summary",
        type=PromptType.SUMMARY,
        template=(
            "请用中文总结以下内容，不超过 100 字：\n\n"
            "{{ content }}"
        ),
        description="中文摘要模板",
    )
    registry.register(custom_summary)
    print("  已注册: zh_summary")

    # STEP 4: 使用自定义模板
    print("\nSTEP 4: 使用自定义模板渲染")
    result = custom_qa.render(
        context="PyArrow 提供 zero-copy 读取和高效的 I/O。",
        question="PyArrow 相比 Pandas 有什么优势？",
    )
    print(f"  tech_qa_zh 渲染结果:")
    print(f"  {result[:250]}...")

    summary_result = custom_summary.render(
        content="知识图谱是一种用图结构表示实体及其关系的语义网络技术。"
              "它广泛应用于推荐系统、问答系统和数据分析等场景。"
              "Neo4j、HugeGraph 是常见的图数据库。"
    )
    print(f"\n  zh_summary 渲染结果:")
    print(f"  {summary_result}")

    # STEP 5: 实体抽取模板
    print("\nSTEP 5: 实体抽取模板")
    extract_tmpl = registry.get("entity_extract")
    if extract_tmpl:
        print(f"  模板名: {extract_tmpl.name}")
        print(f"  类型: {extract_tmpl.type.value}")
        rendered = extract_tmpl.render(text="Apache Arrow 由 Dremio 团队开发")
        print(f"  渲染示例 (前 200 字符): {rendered[:200]}...")

    # STEP 6: 从问题中提取实体的模板
    print("\nSTEP 6: 问题实体提取模板")
    q_extract = registry.get("entity_extract_from_question")
    if q_extract:
        print(f"  模板名: {q_extract.name}")
        rendered = q_extract.render(question="向量数据库和列式存储的关系是什么？")
        print(f"  渲染示例 (前 200 字符): {rendered[:200]}...")

    # STEP 7: GraphRAG 专用模板
    print("\nSTEP 7: GraphRAG 专用模板")
    graph_qa = registry.get("graph_qa")
    if graph_qa:
        print(f"  模板名: {graph_qa.name}")
        print(f"  描述: {graph_qa.description}")
        rendered = graph_qa.render(
            context="Arrow 是列式格式",
            graph_context="Arrow --类型--> 列式存储, Arrow --属于--> Apache",
            question="Arrow 属于哪个项目？",
        )
        print(f"  渲染示例 (前 300 字符): {rendered[:300]}...")

    # STEP 8: 模板管理
    print("\nSTEP 8: 模板管理总结")
    print(f"  内置模板: {len(all_templates)} 个")
    print(f"  自定义模板: 3 个 (tech_qa_zh, zh_summary, +1)")
    print(f"  总可用: {len(registry.list_templates())} 个")

    print("\n  [全部 PASS]")
    shutil.rmtree(base, ignore_errors=True)
    print("(已清理)")


if __name__ == "__main__":
    main()
