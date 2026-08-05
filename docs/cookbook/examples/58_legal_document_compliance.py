#!/usr/bin/env python3
"""法律文档合规系统 — Lineage + Audit + Quality + Dedup + FTS

业务场景: 律师事务所处理合同和法律文档，需要合规审查和溯源。

完整链路:
  1. 创建法律文档数据集 (合同、协议、法规文本)
  2. 质量过滤 (文本长度、字段完整度)
  3. 去重 (精确去重 — 检测重复提交)
  4. 全文搜索 — 检索特定法律条款
  5. 向量搜索 — 相似判例检索
  6. 混合搜索 — 语义 + 关键词联合检索
  7. OLAP 分析 — 文档类型分布、合规率统计
  8. 数据血缘 — 完整处理管线溯源
  9. 审计日志 — 合规操作记录 + HMAC 验证
  10. 审计导出

技术栈覆盖:
  - DuckDB: OLAP GROUP BY, CASE WHEN, HAVING
  - LanceDB: create_dataset, create_index (vector + FTS)
  - Lake SDK: quality_filter, deduplicate, text_search, search,
              hybrid_search, olap_query, lineage, audit, export

前置条件:
  - MinIO 运行中

用法:
    python examples/s3_minio/03_legal_document_compliance.py
"""

from __future__ import annotations

import argparse

import numpy as np
import pyarrow as pa
from arrow_lake import Lake
from arrow_lake.config import ArrowLakeConfig, StorageBackend, StorageConfig

DATASETS = ["legal_docs"]


def _check_minio(endpoint: str) -> bool:
    import urllib.request

    try:
        req = urllib.request.urlopen(f"{endpoint}/minio/health/live", timeout=3)
        return req.status == 200
    except Exception:
        return False


def _make_config(endpoint: str, access_key: str, secret_key: str) -> ArrowLakeConfig:
    return ArrowLakeConfig(
        storage=StorageConfig(
            backend=StorageBackend.MINIO,
            s3_endpoint=endpoint,
            s3_access_key=access_key,
            s3_secret_key=secret_key,
            s3_bucket="arrow-lake",
            s3_region="us-east-1",
        ),
    )


# ---------------------------------------------------------------------------
# Legal document data
# ---------------------------------------------------------------------------

_DOC_TYPES = ["contract", "nda", "license", "regulation", "memo"]

_PARTIES = [
    "Acme Corporation",
    "GlobalTech Inc.",
    "Innovate Labs",
    "Pinnacle Holdings",
    "Vertex Solutions",
    "Nova Enterprises",
    "Summit Partners",
    "Atlas Group",
]

_LEGAL_TEXTS = [
    # Contracts
    "This Service Agreement (the 'Agreement') is entered into as of January 15, 2026, "
    "by and between the parties identified herein. The Service Provider agrees to deliver "
    "software development services in accordance with the Statement of Work attached hereto. "
    "All deliverables shall conform to the specifications set forth in Exhibit A. "
    "Payment terms: net 30 days from invoice date. Late payment shall incur interest "
    "at the rate of 1.5% per month. This Agreement shall be governed by the laws of "
    "the State of Delaware.",
    # NDA
    "NON-DISCLOSURE AGREEMENT: The receiving party agrees to maintain strict "
    "confidentiality of all proprietary information disclosed by the disclosing party. "
    "Confidential information includes trade secrets, business plans, financial data, "
    "customer lists, and technical specifications. The obligation of confidentiality "
    "shall survive for a period of five (5) years from the date of disclosure. "
    "Unauthorized disclosure shall result in injunctive relief and monetary damages.",
    # License
    "SOFTWARE LICENSE AGREEMENT: Subject to the terms and conditions herein, "
    "the licensor grants the licensee a non-exclusive, non-transferable, revocable "
    "license to use the software solely for internal business purposes. The license "
    "is limited to the number of seats specified in the purchase order. Reverse "
    "engineering, decompilation, and disassembly are expressly prohibited.",
    # Regulation
    "REGULATION: All entities subject to this regulation shall maintain comprehensive "
    "records of data processing activities for a minimum of seven years. Personal data "
    "shall be processed only with the explicit consent of the data subject or under "
    "a lawful basis as defined in Article 6. Data controllers shall implement "
    "appropriate technical and organizational measures to ensure data protection.",
    # Memo
    "INTERNAL MEMORANDUM: Subject: Quarterly compliance review. This memo summarizes "
    "the findings of the Q4 2025 compliance audit conducted across all business units. "
    "Key findings include: 3 instances of late contract renewals, 1 potential data "
    "handling violation, and 12 outstanding action items from the previous quarter. "
    "Recommended actions: Implement automated contract tracking system and schedule "
    "mandatory data handling training for all employees by March 2026.",
    # Contract amendment
    "AMENDMENT TO SERVICE AGREEMENT: The parties hereby agree to amend Section 4.2 "
    "of the original Agreement to extend the service term by twelve (12) months, "
    "effective February 1, 2026. All other terms and conditions of the original "
    "Agreement shall remain in full force and effect. This Amendment may be executed "
    "in counterparts, each of which shall be deemed an original.",
    # NDA mutual
    "MUTUAL NON-DISCLOSURE AGREEMENT: Each party agrees to protect the confidential "
    "information of the other party with the same degree of care it uses to protect "
    "its own confidential information, but in no event less than reasonable care. "
    "Confidential information shall not be disclosed to any third party without "
    "prior written consent, except as required by law or court order.",
    # IP assignment
    "INTELLECTUAL PROPERTY ASSIGNMENT: The assignor hereby irrevocably transfers, "
    "assigns, and conveys to the assignee all right, title, and interest in and to "
    "all intellectual property created during the term of engagement, including but "
    "not limited to patents, copyrights, trademarks, and trade secrets. The assignor "
    "waives any and all moral rights in the assigned intellectual property.",
]


def _generate_legal_docs() -> pa.Table:
    """生成模拟法律文档数据."""
    rng = np.random.RandomState(42)
    n = 300
    dim = 64

    doc_ids, titles, doc_types, parties, dates = [], [], [], [], []
    statuses, priorities, texts, char_counts = [], [], [], []

    for i in range(n):
        doc_id = f"DOC_{i:05d}"
        doc_type = _DOC_TYPES[i % len(_DOC_TYPES)]
        party_a = _PARTIES[i % len(_PARTIES)]
        party_b = _PARTIES[(i + 3) % len(_PARTIES)]
        year = 2024 + (i % 3)
        month = (i % 12) + 1
        day = (i % 28) + 1
        date_str = f"{year}-{month:02d}-{day:02d}"

        text = _LEGAL_TEXTS[i % len(_LEGAL_TEXTS)]
        # 模拟质量问题
        if i == 70:
            text = ""  # 空文档
        elif i == 72:
            text = "Short"  # 太短
        elif i == 74:
            text = _LEGAL_TEXTS[0]  # 重复文档

        doc_ids.append(doc_id)
        titles.append(f"{doc_type.upper()}: {party_a} & {party_b} ({date_str})")
        doc_types.append(doc_type)
        parties.append(f"{party_a} <-> {party_b}")
        dates.append(date_str)
        statuses.append(["active", "expired", "draft", "under_review", "approved"][i % 5])
        priorities.append(["high", "medium", "low"][i % 3])
        texts.append(text)
        char_counts.append(len(text))

    # 嵌入
    vectors = rng.randn(n, dim).astype(np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    vectors = vectors / np.where(norms == 0, 1, norms)

    return pa.table(
        {
            "doc_id": doc_ids,
            "title": titles,
            "doc_type": doc_types,
            "parties": parties,
            "date": dates,
            "status": statuses,
            "priority": priorities,
            "text_content": texts,
            "char_count": char_counts,
            "text_embedding": pa.FixedSizeListArray.from_arrays(vectors.ravel(), dim),
        }
    )


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def _step1_ingest(lake: Lake) -> pa.Table:
    """Step 1: 入库法律文档."""
    print("STEP 1: Ingest Legal Documents")
    print("-" * 65)

    docs = _generate_legal_docs()
    lake.create_dataset("legal_docs", docs)
    print(f"  Created 'legal_docs': {docs.num_rows} rows")
    print(f"  Columns: {docs.column_names}")
    print(f"  Doc types: {sorted(set(docs.column('doc_type').to_pylist()))}")
    print("  [PASS]\n")
    return docs


def _step2_quality(lake: Lake) -> None:
    """Step 2: 质量过滤."""
    print("STEP 2: Quality Filter (Text Length)")
    print("-" * 65)

    report = lake.quality_filter("legal_docs", active_filters="text_length")
    print(f"  Filter: text_length | passed={report.passed}, rejected={report.rejected}")

    # OLAP: 查看文档长度分布
    result = lake.olap_query(
        "legal_docs",
        (
            "SELECT doc_type, "
            "COUNT(*) FILTER (WHERE char_count = 0) as empty_docs, "
            "COUNT(*) FILTER (WHERE char_count < 50 AND char_count > 0) as short_docs, "
            "COUNT(*) FILTER (WHERE char_count >= 50) as valid_docs, "
            "AVG(char_count) as avg_length "
            "FROM legal_docs GROUP BY doc_type ORDER BY doc_type"
        ),
    )
    print("  Quality by type:")
    for i in range(result.row_count):
        dtype = result.table.column("doc_type")[i].as_py()
        empty = result.table.column("empty_docs")[i].as_py()
        short = result.table.column("short_docs")[i].as_py()
        valid = result.table.column("valid_docs")[i].as_py()
        avg = result.table.column("avg_length")[i].as_py()
        print(f"    {dtype}: valid={valid}, short={short}, empty={empty}, avg_len={avg:.0f}")

    print("  [PASS]\n")


def _step3_dedup(lake: Lake) -> None:
    """Step 3: 去重."""
    print("STEP 3: Content Deduplication")
    print("-" * 65)

    result = lake.deduplicate("legal_docs", strategy="exact", action="flag")
    print(f"  Strategy: {result.strategy} | Action: {result.action}")
    print(
        f"  Total: {result.total_rows}, Unique: {result.unique_rows}, "
        f"Duplicates: {result.duplicates_found}"
    )

    print("  [PASS]\n")


def _step4_fts(lake: Lake) -> None:
    """Step 4: 全文搜索 — 法律条款检索."""
    print("STEP 4: Full-Text Search (DuckDB lance_fts)")
    print("-" * 65)

    lake.create_fts_index("legal_docs", fts_column="text_content")
    print("  FTS index created on 'content'")

    queries = [
        ("confidentiality trade secrets", "机密条款"),
        ("software license non-exclusive", "软件许可"),
        ("intellectual property assignment", "知识产权转让"),
        ("data protection personal data consent", "数据保护"),
        ("payment terms net 30 days", "付款条款"),
    ]
    for query, desc in queries:
        result = lake.text_search("legal_docs", query, top_k=3, fts_column="text_content")
        print(f"  [{desc}] '{query}': {result.row_count} results")
        for i in range(min(result.row_count, 2)):
            did = result.table.column("doc_id")[i].as_py()
            score = result.table.column("_score")[i].as_py()
            dtype = result.table.column("doc_type")[i].as_py()
            print(f"    {did} ({dtype}): score={score:.4f}")

    print("  [PASS]\n")


def _step5_vector_hybrid(lake: Lake, dim: int = 64) -> None:
    """Step 5: 向量 + 混合搜索."""
    print("STEP 5: Vector + Hybrid Search")
    print("-" * 65)

    rng = np.random.RandomState(42)
    query_vec = rng.randn(dim).astype(np.float32)
    query_vec = query_vec / np.linalg.norm(query_vec)

    # 5a. 向量搜索 — 相似判例
    lake.create_vector_index(
        "legal_docs", metric="cosine", vector_column="text_embedding", num_sub_vectors=8
    )
    vs_result = lake.search("legal_docs", query_vec.tolist(), top_k=5)
    print(f"  [Vector] Similar documents: {vs_result.row_count} results")
    for i in range(min(vs_result.row_count, 3)):
        did = vs_result.table.column("doc_id")[i].as_py()
        dtype = vs_result.table.column("doc_type")[i].as_py()
        dist = vs_result.table.column("_distance")[i].as_py()
        print(f"    {did} ({dtype}): distance={dist:.4f}")

    # 5b. 混合搜索 — 语义 + 关键词
    hybrid_result = lake.hybrid_search(
        "legal_docs",
        query_vec.tolist(),
        "confidentiality trade secret protection",
        top_k=5,
        fts_column="text_content",
    )
    print(
        f"\n  [Hybrid] 'confidentiality trade secret protection': {hybrid_result.row_count} results"
    )
    print(f"    RRF k={hybrid_result.rrf_k}, max_score={hybrid_result.max_rrf_score}")
    score_col = "_hybrid_score" if "_hybrid_score" in hybrid_result.table.column_names else "_rrf_score"
    for i in range(min(hybrid_result.row_count, 3)):
        did = hybrid_result.table.column("doc_id")[i].as_py()
        score = hybrid_result.table.column(score_col)[i].as_py()
        dtype = hybrid_result.table.column("doc_type")[i].as_py()
        print(f"    {did} ({dtype}): rrf={score:.4f}")

    # 5c. WHERE 过滤 — 只搜合同
    filtered = lake.search(
        "legal_docs",
        query_vec.tolist(),
        top_k=3,
        where="doc_type = 'contract'",
    )
    print(f"\n  [Vector+WHERE] contracts only: {filtered.row_count} results")
    for i in range(min(filtered.row_count, 3)):
        did = filtered.table.column("doc_id")[i].as_py()
        dist = filtered.table.column("_distance")[i].as_py()
        print(f"    {did}: distance={dist:.4f}")

    print("  [PASS]\n")


def _step6_olap(lake: Lake) -> None:
    """Step 6: OLAP — 合规统计."""
    print("STEP 6: OLAP Compliance Analytics (DuckDB)")
    print("-" * 65)

    # 6a. 文档类型 × 状态 分布
    result = lake.olap_query(
        "legal_docs",
        (
            "SELECT doc_type, status, COUNT(*) as count, "
            "AVG(char_count) as avg_length "
            "FROM legal_docs GROUP BY doc_type, status "
            "ORDER BY doc_type, status"
        ),
    )
    print("  Document type x status:")
    for i in range(result.row_count):
        dtype = result.table.column("doc_type")[i].as_py()
        status = result.table.column("status")[i].as_py()
        cnt = result.table.column("count")[i].as_py()
        print(f"    {dtype} / {status}: {cnt}")

    # 6b. 合规率 — 按类型统计 active+approved 占比
    result2 = lake.olap_query(
        "legal_docs",
        (
            "SELECT doc_type, "
            "COUNT(*) as total, "
            "SUM(CASE WHEN status IN ('active', 'approved') THEN 1 ELSE 0 END) as compliant, "
            "ROUND(SUM(CASE WHEN status IN ('active', 'approved') THEN 1.0 ELSE 0.0 END) "
            "* 100.0 / COUNT(*), 2) as compliance_rate "
            "FROM legal_docs GROUP BY doc_type ORDER BY compliance_rate DESC"
        ),
    )
    print("\n  Compliance rate by type:")
    for i in range(result2.row_count):
        dtype = result2.table.column("doc_type")[i].as_py()
        total = result2.table.column("total")[i].as_py()
        comp = result2.table.column("compliant")[i].as_py()
        rate = result2.table.column("compliance_rate")[i].as_py()
        print(f"    {dtype}: {comp}/{total} = {rate}%")

    # 6c. 高优先级文档
    result3 = lake.olap_query(
        "legal_docs",
        (
            "SELECT doc_id, doc_type, priority, status, char_count "
            "FROM legal_docs WHERE priority = 'high' AND status != 'approved' "
            "ORDER BY char_count DESC LIMIT 5"
        ),
    )
    print(f"\n  High-priority pending actions: {result3.row_count} docs")
    for i in range(result3.row_count):
        did = result3.table.column("doc_id")[i].as_py()
        dtype = result3.table.column("doc_type")[i].as_py()
        status = result3.table.column("status")[i].as_py()
        print(f"    {did} ({dtype}): {status}")

    print("  [PASS]\n")


def _step7_lineage(lake: Lake) -> None:
    """Step 7: 合规血缘追踪."""
    print("STEP 7: Compliance Lineage Tracking")
    print("-" * 65)

    pipeline_steps = [
        (
            "create",
            "document_ingest",
            "compliance_system",
            {"source": "email_attachment", "format": "pdf"},
        ),
        ("transform", "ocr_extraction", "ocr_service", {"engine": "tesseract", "accuracy": "0.97"}),
        (
            "transform",
            "entity_extraction",
            "nlp_service",
            {"entities": ["party", "date", "amount"]},
        ),
        (
            "transform",
            "quality_check",
            "quality_service",
            {"rules": ["text_length", "schema_valid"]},
        ),
        ("transform", "deduplication", "quality_service", {"strategy": "exact", "removed": 1}),
        (
            "transform",
            "classification",
            "ml_service",
            {"model": "legal-bert", "confidence": "0.94"},
        ),
        (
            "transform",
            "compliance_review",
            "legal_team",
            {"reviewer": "Alice Chen", "status": "approved"},
        ),
    ]
    for op, ttype, actor, meta in pipeline_steps:
        lake.lineage_record_event(
            "legal_docs", op, transform_type=ttype, actor=actor, metadata=meta
        )

    history = lake.lineage_history("legal_docs")
    print(f"  Pipeline: {len(history)} steps recorded")
    for e in history:
        meta = dict(e.metadata) if e.metadata else {}
        print(f"    [{e.operation}] {e.transform_type} by {e.actor}")

    # SQL 追踪
    lineage_result = lake.lineage_query(
        "SELECT transform_type, actor, COUNT(*) as step_count "
        "FROM _lineage_events WHERE dataset_name = 'legal_docs' "
        "GROUP BY transform_type, actor"
    )
    if hasattr(lineage_result, "num_rows") and lineage_result.num_rows > 0:
        print(f"  Lineage SQL summary: {lineage_result.num_rows} groups")

    print("  [PASS]\n")


def _step8_audit(lake: Lake) -> None:
    """Step 8: 合规审计."""
    print("STEP 8: Compliance Audit (HMAC)")
    print("-" * 65)

    audit_events = [
        (
            "document_reviewed",
            "legal_docs",
            "lawyer_bob",
            {"verdict": "approved", "risk_level": "low"},
        ),
        (
            "document_flagged",
            "legal_docs",
            "compliance_system",
            {"reason": "ambiguous_clause", "section": "4.2"},
        ),
        ("access_granted", "legal_docs", "admin", {"user": "lawyer_bob", "role": "reviewer"}),
        ("document_exported", "legal_docs", "lawyer_bob", {"format": "pdf", "pages": 12}),
        (
            "policy_violation",
            "legal_docs",
            "compliance_system",
            {"violation": "unsigned_nda", "severity": "high"},
        ),
    ]
    audit_ids = []
    for event_type, dataset, actor, payload in audit_events:
        aid = lake.audit_record(event_type, dataset_name=dataset, actor=actor, payload=payload)
        audit_ids.append(aid)
        print(f"  Recorded: {event_type} by {actor} -> {aid[:16]}...")

    # HMAC 验证
    print("\n  HMAC verification:")
    for aid in audit_ids:
        ok = lake.audit_verify(aid)
        print(f"    {aid[:8]}...: {'OK' if ok else 'TAMPERED'}")

    # 查询
    violations = lake.audit_query(event_type="policy_violation")
    print(f"\n  Policy violations: {len(violations)}")

    exports = lake.audit_export("legal_docs")
    print(f"  Audit export: {len(exports.get('entries', []))} entries")

    print("  [PASS]\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Legal Document Compliance E2E")
    parser.add_argument("--endpoint", default="http://localhost:9000")
    parser.add_argument("--access-key", default="minioadmin")
    parser.add_argument("--secret-key", default="minioadmin")
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()

    print("=" * 65)
    print("Arrow Lake v1.2 — Legal Document Compliance (Full Stack E2E)")
    print("=" * 65)
    print()

    if not _check_minio(args.endpoint):
        print(f"[SKIP] MinIO not reachable at {args.endpoint}")
        return

    config = _make_config(args.endpoint, args.access_key, args.secret_key)

    try:
        lake = Lake(base_uri="datasets", config=config)
        print(f"Backend: {config.storage.backend} | URI: {config.storage.s3_uri}\n")

        _step1_ingest(lake)
        _step2_quality(lake)
        _step3_dedup(lake)
        _step4_fts(lake)
        _step5_vector_hybrid(lake)
        _step6_olap(lake)
        _step7_lineage(lake)
        _step8_audit(lake)

    except Exception as exc:
        print(f"\n[ERROR] {exc}")
        import traceback

        traceback.print_exc()
        return
    finally:
        if not args.no_cleanup:
            lake = Lake(base_uri="datasets", config=config)
            for ds in DATASETS:
                if ds in lake.list_datasets():
                    lake.delete_dataset(ds)
            print("\n[cleanup] Done")

    print("=" * 65)
    print("All 8 steps passed!")
    print("=" * 65)


if __name__ == "__main__":
    main()
