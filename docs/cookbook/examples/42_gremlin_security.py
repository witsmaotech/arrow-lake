#!/usr/bin/env python3
"""42 — Gremlin 查询白名单安全

场景: 演示 Gremlin 查询白名单验证机制。只允许只读遍历步骤,
     拦截变更操作 (drop/addV/property 等) 和注入攻击。

依赖: 无额外依赖 (白名单验证为纯 Python 实现)
"""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

try:
    from arrow_lake import Lake
except ImportError as exc:
    print(f"导入失败: {exc}")
    print("请安装 arrow_lake:  pip install -e .")
    raise SystemExit(1)


_DEFAULT_BASE_URI = "./_tmp_gremlin_security"

# 复制自 arrow_lake.api.routers.knowledge_graph (保持同步)
_ALLOWED_GREMLIN_STEPS = frozenset({
    "traversal",
    "V", "E", "has", "hasLabel", "hasId", "hasNot",
    "out", "in", "both", "outE", "inE", "bothE", "outV", "inV",
    "values", "valueMap", "elementMap", "properties",
    "count", "limit", "range", "order", "by",
    "select", "as", "where", "path", "dedup",
    "group", "groupCount", "project", "union", "fold",
    "sum", "mean", "max", "min",
    "id", "label", "constant",
    "repeat", "simplePath", "times", "until", "emit", "loops",
    "cyclicPath", "is", "not", "coin", "sample",
})

_GREMLIN_STEP_RE = re.compile(r"\.\s*(\w+)\s*\(")
_COMMENT_RE = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)

_FORBIDDEN_BARE_RE = re.compile(
    r"\.\s*drop\b|\.\s*addV\b|\.\s*addE\b|\.\s*property\b|\.\s*remove\b|\.\s*delete\b",
    re.IGNORECASE,
)


def _validate_gremlin(query: str) -> tuple[bool, str]:
    """验证 Gremlin 查询是否只使用白名单步骤。

    Returns:
        (is_valid, detail) 元组
    """
    cleaned = _COMMENT_RE.sub("", query)
    if "{" in cleaned or "}" in cleaned:
        return False, "Closure syntax not allowed in Gremlin queries"
    if _FORBIDDEN_BARE_RE.search(cleaned):
        return False, "Mutation steps are forbidden in Gremlin queries"
    for match in _GREMLIN_STEP_RE.finditer(cleaned):
        step = match.group(1)
        if step not in _ALLOWED_GREMLIN_STEPS:
            return False, f"Forbidden Gremlin step: {step}"
    return True, "OK"


def main() -> None:
    parser = argparse.ArgumentParser(description="42_gremlin_security.py")
    parser.add_argument("--base-uri", default=_DEFAULT_BASE_URI)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    no_cleanup = args.no_cleanup
    print("=" * 60)
    print("42 Gremlin 查询白名单安全")
    print("=" * 60)

    base = Path(args.base_uri)
    if base.exists():
        shutil.rmtree(base)

    lake = Lake(base_uri=args.base_uri)

    # STEP 1: 白名单步骤一览
    print("\nSTEP 1: 允许的 Gremlin 步骤 (白名单)")
    categories = {
        "顶点/边遍历": ["V", "E", "out", "in", "both", "outE", "inE", "bothE", "outV", "inV"],
        "属性过滤": ["has", "hasLabel", "hasId", "hasNot"],
        "属性读取": ["values", "valueMap", "elementMap", "properties", "id", "label"],
        "聚合统计": ["count", "group", "groupCount", "sum", "mean", "max", "min", "fold"],
        "路径/排序": ["path", "order", "by", "limit", "range", "dedup", "select", "as", "where"],
        "其他": ["project", "union", "constant"],
    }
    for cat, steps in categories.items():
        print(f"  {cat}: {', '.join(steps)}")

    # STEP 2: 合法查询演示
    print("\nSTEP 2: 合法查询 (通过验证)")
    valid_queries = [
        ("顶点查找", 'hugegraph.traversal().V().has("name",eq("Alice"))'),
        ("邻居遍历", 'hugegraph.traversal().V("v1").repeat(out()).simplePath().times(2)'),
        ("属性读取", 'hugegraph.traversal().V().hasLabel("Person").values("name")'),
        ("计数统计", 'hugegraph.traversal().V().count()'),
        ("分组统计", 'hugegraph.traversal().V().groupCount().by(label)'),
        ("子图获取", 'hugegraph.traversal().V("v1").repeat(both()).simplePath().times(2)'),
    ]
    for desc, query in valid_queries:
        ok, detail = _validate_gremlin(query)
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {desc}: {query[:60]}{'...' if len(query) > 60 else ''}")

    # STEP 3: 被拦截的查询
    print("\nSTEP 3: 被拦截的查询 (变更操作)")
    blocked_queries = [
        ("删除顶点", 'hugegraph.traversal().V("v1").drop()'),
        ("添加顶点", 'hugegraph.traversal().addV("Person").property("name","X")'),
        ("修改属性", 'hugegraph.traversal().V("v1").property("name","X")'),
        ("注入攻击", 'hugegraph.traversal().inject("malicious").map{it.drop}'),
        ("闭包执行", 'hugegraph.traversal().V().map{ Thread.sleep(10000) }'),
        ("迭代执行", 'hugegraph.traversal().V().drop().iterate()'),
    ]
    for desc, query in blocked_queries:
        ok, detail = _validate_gremlin(query)
        status = "BLOCKED" if not ok else "LEAKED"
        print(f"  [{status}] {desc}: {detail}")

    # STEP 4: 注释注入拦截
    print("\nSTEP 4: 注释注入拦截")
    injection_queries = [
        ("注释包裹 drop", 'hugegraph.traversal().V()/* */.drop()'),
        ("多行注释注入", 'hugegraph.traversal().V()/* drop\nmalicious\n*/.has("name",eq("A"))'),
        ("行注释绕过", 'hugegraph.traversal().V() // drop()\n.has("name",eq("A"))'),
    ]
    for desc, query in injection_queries:
        ok, detail = _validate_gremlin(query)
        status = "BLOCKED" if not ok else "PASS"
        print(f"  [{status}] {desc}: {detail}")

    # STEP 5: 多行查询处理
    print("\nSTEP 5: 多行查询处理")
    multiline_queries = [
        ("合法多行", """hugegraph.traversal()
            .V()
            .hasLabel("Person")
            .has("name", eq("Alice"))
            .out("knows")
            .values("name")"""),
        ("非法多行", """hugegraph.traversal()
            .V("v1")
            .drop()"""),
    ]
    for desc, query in multiline_queries:
        ok, detail = _validate_gremlin(query)
        status = "PASS" if ok else "BLOCKED"
        print(f"  [{status}] {desc}: {detail}")

    # STEP 6: 闭包语法拦截
    print("\nSTEP 6: 闭包语法拦截 (v1.3.0)")
    closure_queries = [
        ("Groovy 闭包", 'hugegraph.traversal().V().map{it.get().property("name") }'),
        ("花括号注入", 'hugegraph.traversal().V(){ ${Runtime.exec("rm -rf /")} }'),
        ("flatMap 闭包", 'hugegraph.traversal().V().flatMap{ it.out() }'),
    ]
    for desc, query in closure_queries:
        ok, detail = _validate_gremlin(query)
        status = "BLOCKED" if not ok else "LEAKED"
        print(f"  [{status}] {desc}: {detail}")

    print("\n  [全部 PASS]")
    if not no_cleanup:
        lake.shutdown()
        shutil.rmtree(base, ignore_errors=True)
        print("(已清理)")


if __name__ == "__main__":
    main()
