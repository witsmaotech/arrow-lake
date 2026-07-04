#!/usr/bin/env python3
"""芜湖市城市生命线安全工程 — v1.8.6 端到端业务案例

真实 14MB 中文 PDF（552 页，pypdf 预提取为 552 块 jsonl）走完整 Arrow Lake 链路：
  STEP 1  文档摄入        lake.ingest(jsonl)  [pypdf 预提取的真实 PDF 文本]
  STEP 2  真实向量嵌入     bge-m3(Ollama) embed_and_add + FTS + IVF_PQ 索引
  STEP 3  检索验证         text_search / search / hybrid_search
  STEP 4  KG 全量构建      kg_build(全量 chunk LLM 抽取) → 轮询完成
  STEP 5  KG 统计遍历      kg_stats / entity_type_counts / kneighbor
  STEP 6  纯向量 RAG       rag_query 检索 + LLM 生成
  STEP 7  GraphRAG         rag_query 自动 KG 增强 (per-dataset 图)

容器服务（MinIO + HugeGraph + Ollama）持久化运行，验证 v1.8.6 per-dataset KG 隔离。

前置：宿主先跑 prepare_pdf.py 生成 datas/wuhu_lifeline.jsonl。
运行方式见同目录 README.md（容器网络内跑）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from arrow_lake import Lake

DATASET_DEFAULT = "wuhu_lifeline"
EXAMPLES_BUSI_DIR = Path(os.environ.get("BUSI_EXAMPLES_DIR", Path(__file__).resolve().parent))
JSONL_PATH = EXAMPLES_BUSI_DIR / "datas" / "wuhu_lifeline.jsonl"
RESULTS = Path(os.environ.get("BUSI_RESULTS_DIR", EXAMPLES_BUSI_DIR / "results"))
BASE_URI = "/tmp/al-busi"  # 容器内可写；minio 后端下仅作本地缓存

# 文本列名（jsonl 摄入后列名 = text_content，区别于 PDF ingest_documents 的 text）
TEXT_COL = "text_content"
EMBED_COL = "text_embedding"

# 业务检索/问答语料（贴合城市生命线工程领域）
SEARCH_KEYWORDS = ["城市生命线", "燃气安全", "桥梁监测", "物联网", "应急处置"]
RAG_QUESTIONS = [
    "芜湖市城市生命线安全工程一期建设的总体目标和覆盖范围是什么？",
    "工程覆盖了哪些专项监测领域（如燃气、桥梁、供水、排水等）？",
    "项目采用了什么样的总体技术架构和物联网感知方案？",
]
GRAPH_QUESTIONS = [
    "城市生命线安全工程涉及哪些核心子系统，它们之间的数据流向是什么？",
    "监测预警平台与各专项（燃气/桥梁/供水）是如何联动的？",
]


# -------------------- 工具 --------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _save(step: str, data: dict) -> None:
    try:
        RESULTS.mkdir(parents=True, exist_ok=True)
        (RESULTS / f"{step}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
    except Exception as e:
        print(f"  [warn] 结果落盘失败 {step}: {e}")


def _make_lake() -> Lake:
    """配置全部从 .env 环境变量读（storage=minio, hugegraph, embedding=bge-m3）。"""
    return Lake(base_uri=BASE_URI)


# -------------------- STEP 1: 文档摄入（jsonl） --------------------
def step1_ingest(lake: Lake, dataset: str) -> dict:
    print("=" * 60)
    print("STEP 1  文档摄入 (jsonl: pypdf 预提取的真实 PDF 文本)")
    print("=" * 60)
    if not JSONL_PATH.exists():
        raise FileNotFoundError(f"jsonl 不存在: {JSONL_PATH}（先在宿主跑 prepare_pdf.py）")
    n_lines = sum(1 for _ in JSONL_PATH.open(encoding="utf-8"))
    print(f"  jsonl: {JSONL_PATH.name} ({JSONL_PATH.stat().st_size / 1024:.0f} KB, {n_lines} 行)")

    try:
        lake.delete_dataset(dataset)
    except Exception:
        pass

    t0 = time.time()
    report = lake.ingest(dataset, [str(JSONL_PATH)])
    dt = time.time() - t0
    rows = getattr(report, "total_rows", 0) or 0
    files = getattr(report, "total_files", 0) or 0
    print(f"  摄入完成: {rows} 行 / {files} 文件, 耗时 {dt:.0f}s")

    out = {
        "dataset": dataset, "rows": rows, "files": files, "elapsed_sec": round(dt, 1),
        "source": "pypdf 提取 552 页 PDF → tiktoken 切块", "timestamp": _now_iso(),
    }
    _save("01_ingest", out)
    return out


# -------------------- STEP 2: 真实 embedding + 索引 --------------------
def step2_embed(lake: Lake, dataset: str) -> dict:
    print("\n" + "=" * 60)
    print("STEP 2  真实向量嵌入 (bge-m3) + FTS/向量索引")
    print("=" * 60)
    t0 = time.time()
    n = lake.embed_and_add(dataset, text_column=TEXT_COL, embedding_column=EMBED_COL)
    print(f"  嵌入 {n} 行 (bge-m3, in-place add_columns)")

    idx_v = False
    try:
        # bge-m3 = 1024 维；num_sub_vectors 必须整除维度（1024/32=32）
        lake.create_vector_index(dataset, vector_column=EMBED_COL, num_sub_vectors=32)
        idx_v = True
        print("  向量索引 (IVF_PQ, 32 sub_vectors) 已建立")
    except Exception as e:
        print(f"  向量索引跳过: {e}")

    idx_f = False
    try:
        lake.create_fts_index(dataset, fts_column=TEXT_COL)
        idx_f = True
        print("  FTS 索引已建立")
    except Exception as e:
        print(f"  FTS 索引跳过: {e}")

    dt = time.time() - t0
    out = {
        "embedded_rows": n, "vector_index": idx_v, "fts_index": idx_f,
        "elapsed_sec": round(dt, 1), "timestamp": _now_iso(),
    }
    _save("02_embed_index", out)
    return out


# -------------------- STEP 3: 检索验证 --------------------
def step3_search(lake: Lake, dataset: str) -> dict:
    print("\n" + "=" * 60)
    print("STEP 3  检索验证 (全文 / 向量 / 混合)")
    print("=" * 60)
    out: dict = {"text_search": {}, "vector_search": {}, "hybrid_search": {}}

    # 全文搜索
    for kw in SEARCH_KEYWORDS:
        try:
            r = lake.text_search(dataset, kw, top_k=3, fts_column=TEXT_COL)
            samples = []
            tbl = getattr(r, "table", None)
            rc = getattr(r, "row_count", 0) or 0
            if tbl is not None and rc and TEXT_COL in tbl.column_names:
                for i in range(min(3, rc)):
                    samples.append(str(tbl.column(TEXT_COL)[i].as_py())[:100])
            out["text_search"][kw] = {"count": rc, "samples": samples}
            print(f"  [FTS] '{kw}' → {rc} 条")
        except Exception as e:
            out["text_search"][kw] = {"error": str(e)}
            print(f"  [FTS] '{kw}' 失败: {e}")

    # 向量 + 混合（需编码查询）
    try:
        from arrow_lake.embed.encoder import ApiEmbeddingEncoder

        cfg = lake._config.embedding
        enc = ApiEmbeddingEncoder(
            api_base=cfg.api_base, api_key=cfg.api_key,
            model_name=cfg.model, batch_size=8,
        )
        for kw in SEARCH_KEYWORDS[:3]:
            try:
                vec = enc.encode([kw]).embeddings.tolist()[0]
            except Exception as e:
                out["vector_search"][kw] = {"encode_error": str(e)}
                continue
            try:
                r = lake.search(dataset, vec, top_k=3, vector_column=EMBED_COL)
                rc = getattr(r, "row_count", 0) or 0
                out["vector_search"][kw] = {"count": rc}
                print(f"  [VEC] '{kw}' → {rc} 条")
            except Exception as e:
                out["vector_search"][kw] = {"error": str(e)}
            try:
                r = lake.hybrid_search(
                    dataset, vec, kw, top_k=3,
                    vector_column=EMBED_COL, fts_column=TEXT_COL,
                )
                rc = getattr(r, "row_count", 0) or 0
                out["hybrid_search"][kw] = {"count": rc}
                print(f"  [HYB] '{kw}' → {rc} 条")
            except Exception as e:
                out["hybrid_search"][kw] = {"error": str(e)}
    except Exception as e:
        out["vector_search"] = {"error": f"encoder 不可用: {e}"}
        print(f"  向量编码器不可用: {e}")

    _save("03_search", out)
    return out


# -------------------- STEP 4: KG 全量构建 --------------------
async def step4_kg_build(lake: Lake, dataset: str) -> dict:
    print("\n" + "=" * 60)
    print("STEP 4  知识图谱全量构建 (LLM 抽取, 耗时较长)")
    print("=" * 60)
    # 清旧图（per-dataset 图 kg_{dataset}，避免重跑产生重复实体）
    try:
        await lake.kg_delete_graph(dataset)
        print(f"  已清旧图 (kg_{dataset})")
    except Exception as e:
        print(f"  清图跳过: {e}")

    t0 = time.time()
    try:
        task_id = await lake.kg_build(dataset)
        print(f"  构建任务已提交: {task_id}")
    except Exception as e:
        print(f"  构建提交失败: {e}")
        out = {"error": str(e), "timestamp": _now_iso()}
        _save("04_kg_build", out)
        return out

    # 轮询状态（最长 ~90 min）
    final = None
    for i in range(540):  # 540 × 10s = 90min
        try:
            st = await lake.kg_build_status(task_id)
        except Exception:
            st = None
        if st:
            s = str(st.get("status", "unknown"))
            pc = st.get("processed_chunks", 0)
            tc = st.get("total_chunks", 0)
            ec = st.get("entity_count", 0)
            rc = st.get("relation_count", 0)
            if i % 6 == 0 or s.upper() in ("COMPLETED", "FAILED", "SUCCESS", "DONE"):
                print(f"  [{i * 10:>4}s] {s} | {pc}/{tc} chunks | {ec} 实体 | {rc} 关系")
            final = st
            if s.upper() in ("COMPLETED", "FAILED", "SUCCESS", "DONE"):
                break
        await asyncio.sleep(10)

    dt = time.time() - t0
    out = {"task_id": task_id, "status": final, "elapsed_sec": round(dt, 1), "timestamp": _now_iso()}
    _save("04_kg_build", out)
    st_final = (final or {}).get("status", "unknown")
    print(f"  构建 {st_final}, 耗时 {dt:.0f}s")
    return out


# -------------------- STEP 5: KG 统计 + 遍历 --------------------
async def step5_kg_traversal(lake: Lake, dataset: str) -> dict:
    print("\n" + "=" * 60)
    print("STEP 5  知识图谱统计与遍历")
    print("=" * 60)
    out: dict = {}

    try:
        stats = await lake.kg_stats()
        out["stats"] = stats
        print(f"  顶点: {stats.get('total_vertices', 0)}, 边: {stats.get('total_edges', 0)}")
    except Exception as e:
        out["stats_error"] = str(e)
        print(f"  统计失败: {e}")

    try:
        from arrow_lake.knowledge_graph.queries import GremlinQueries

        r = await lake.kg_query(GremlinQueries.entity_type_counts())
        out["entity_types"] = r[:15] if isinstance(r, list) else r
        print("  实体类型分布:")
        if isinstance(r, list):
            for x in r[:10]:
                if isinstance(x, dict):
                    print(f"    {x.get('label', x.get('VertexLabel', '?'))}: {x.get('count', '?')}")
    except Exception as e:
        out["entity_types_error"] = str(e)
        print(f"  类型统计失败: {e}")

    # 顶点样本（g.V 取真实顶点）+ 用真实顶点 id 做 kneighbor
    out["neighbors"] = {}
    real_seeds: list[str] = []
    try:
        verts = await lake.kg_query("g.V().limit(20).valueMap(true)")
        verts = verts or []
        out["vertex_sample_count"] = len(verts)
        print(f"  顶点样本 (g.V): {len(verts)} 个, 前 8 个:")
        for v in verts[:8]:
            if isinstance(v, dict):
                nm = v.get("name") or v.get("实体名") or v.get("名称") or ""
                if isinstance(nm, list):
                    nm = nm[0] if nm else ""
                label = v.get("label", "")
                if isinstance(label, list):
                    label = label[0] if label else ""
                vid = v.get("id")
                print(f"    [{label}] {nm or vid}")
                if vid is not None and len(real_seeds) < 5:
                    real_seeds.append(str(vid))
    except Exception as e:
        out["vertex_sample_error"] = str(e)
        print(f"  顶点样本失败: {e}")

    for seed in real_seeds[:3]:
        try:
            nb = await lake.kg_get_neighbors(seed, depth=1)
            cnt = len(nb) if isinstance(nb, list) else "n/a"
            out["neighbors"][seed[:40]] = cnt
            print(f"  vertex {seed[:30]} 一阶邻居: {cnt} 个")
        except Exception as e:
            out["neighbors"][seed[:40]] = f"error: {str(e)[:80]}"

    _save("05_kg_traversal", out)
    return out


# -------------------- STEP 6: 纯向量 RAG --------------------
async def step6_rag(lake: Lake, dataset: str) -> dict:
    print("\n" + "=" * 60)
    print("STEP 6  纯向量 RAG 问答")
    print("=" * 60)
    return await _ask(lake, dataset, RAG_QUESTIONS, "06_rag_qa", prefix="busi_rag")


# -------------------- STEP 7: GraphRAG --------------------
async def step7_graphrag(lake: Lake, dataset: str) -> dict:
    print("\n" + "=" * 60)
    print("STEP 7  GraphRAG 联合问答 (KG + 向量, per-dataset)")
    print("=" * 60)
    return await _ask(lake, dataset, GRAPH_QUESTIONS, "07_graphrag_qa", prefix="busi_graphrag")


async def _ask(lake: Lake, dataset: str, questions: list[str], step_file: str, prefix: str) -> dict:
    out = {"questions": []}
    sid = f"{prefix}_{int(time.time())}"
    for q in questions:
        t0 = time.time()
        try:
            resp = await lake.rag_query(q, dataset, top_k=5, session_id=sid)
            ans = getattr(resp, "answer", "") or str(resp)
            cits = getattr(resp, "citations", [])
            cit_n = len(cits) if isinstance(cits, list) else cits
            tok = getattr(resp, "context_tokens", None)
            dt = time.time() - t0
            item = {
                "q": q, "a": ans[:600], "citations": cit_n,
                "context_tokens": tok, "latency_ms": round(dt * 1000, 0),
            }
            print(f"  Q: {q[:50]}...")
            print(f"  A: {ans[:200]}...")
            print(f"     引用 {cit_n}, {dt:.1f}s")
        except Exception as e:
            item = {"q": q, "error": str(e)}
            print(f"  Q: {q[:50]}... → 失败: {e}")
        out["questions"].append(item)
    out["timestamp"] = _now_iso()
    _save(step_file, out)
    return out


# -------------------- 汇总 --------------------
def gen_summary(collected: dict) -> None:
    lines = [
        "# 芜湖市城市生命线安全工程 — v1.8.6 端到端测试汇总",
        "",
        f"_生成时间: {_now_iso()}_",
        "",
    ]
    for name, v in collected.items():
        lines.append(f"## {name}")
        body = json.dumps(v, ensure_ascii=False, indent=2, default=str)
        if len(body) > 1800:
            body = body[:1800] + "\n... (截断)"
        lines.append(f"```json\n{body}\n```\n")

    lines.append("## 关键指标")
    s1 = collected.get("STEP1", {})
    s2 = collected.get("STEP2", {})
    s4 = collected.get("STEP4", {})
    s5 = collected.get("STEP5", {})
    if isinstance(s1, dict):
        lines.append(f"- 摄入行数: {s1.get('rows', '?')} ({s1.get('elapsed_sec', '?')}s)")
    if isinstance(s2, dict):
        lines.append(f"- 嵌入行数: {s2.get('embedded_rows', '?')} (向量索引={s2.get('vector_index')}, FTS={s2.get('fts_index')})")
    if isinstance(s4, dict) and isinstance(s4.get("status"), dict):
        st = s4["status"]
        lines.append(f"- KG: {st.get('entity_count', '?')} 实体 / {st.get('relation_count', '?')} 关系 ({st.get('status')})")
    if isinstance(s5, dict) and isinstance(s5.get("stats"), dict):
        st = s5["stats"]
        lines.append(f"- 图统计: {st.get('total_vertices', '?')} 顶点 / {st.get('total_edges', '?')} 边")

    try:
        RESULTS.mkdir(parents=True, exist_ok=True)
        (RESULTS / "e2e_summary.md").write_text("\n".join(lines), encoding="utf-8")
        print(f"\n汇总报告: {RESULTS / 'e2e_summary.md'}")
    except Exception as e:
        print(f"\n[warn] 汇总落盘失败: {e}")
        print("\n".join(lines[:20]))


# -------------------- 主流程 --------------------
async def main_async() -> None:
    ap = argparse.ArgumentParser(description="芜湖城市生命线 v1.8.6 端到端业务案例")
    ap.add_argument("--dataset", default=DATASET_DEFAULT)
    ap.add_argument("--no-cleanup", action="store_true", help="保留 dataset (持久化到 MinIO)")
    ap.add_argument("--skip-ingest", action="store_true", help="跳过 STEP 1")
    ap.add_argument("--skip-kg", action="store_true", help="跳过 STEP 4 KG 构建")
    ap.add_argument("--from-step", type=int, default=1)
    ap.add_argument("--until-step", type=int, default=7)
    args = ap.parse_args()
    dataset = args.dataset

    print("=" * 60)
    print("芜湖市城市生命线安全工程 — v1.8.6 端到端业务案例")
    print(f"dataset = {dataset} | steps = {args.from_step}..{args.until_step}")
    print("=" * 60)

    lake = _make_lake()
    collected: dict = {}

    steps = [
        (1, "STEP1", lambda: step1_ingest(lake, dataset)),
        (2, "STEP2", lambda: step2_embed(lake, dataset)),
        (3, "STEP3", lambda: step3_search(lake, dataset)),
        (4, "STEP4", lambda: step4_kg_build(lake, dataset)),
        (5, "STEP5", lambda: step5_kg_traversal(lake, dataset)),
        (6, "STEP6", lambda: step6_rag(lake, dataset)),
        (7, "STEP7", lambda: step7_graphrag(lake, dataset)),
    ]

    for n, name, fn in steps:
        if n < args.from_step:
            continue
        if n > args.until_step:
            break
        if n == 1 and args.skip_ingest:
            continue
        if n == 4 and args.skip_kg:
            continue
        try:
            res = fn()
            if asyncio.iscoroutine(res):
                res = await res
            collected[name] = res
        except Exception as e:
            print(f"\n  [!] {name} 失败: {e}")
            traceback.print_exc()
            collected[name] = {"error": str(e)}
            if n == 1:
                print("  摄入失败，无法继续后续步骤")
                break

    gen_summary(collected)

    if not args.no_cleanup:
        print("\n清理数据集...")
        try:
            lake.delete_dataset(dataset)
        except Exception:
            pass
    lake.shutdown()
    print("\n  [全部完成]")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
