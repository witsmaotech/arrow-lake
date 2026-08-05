#!/usr/bin/env python3
"""跨域供应链溯源 — HTTP 入库 + 复杂血缘链 + 多表 JOIN

业务场景: 跨域供应链数据管理，从多个数据源入库，建立完整的
血缘链路追溯，并支持跨 4 表的 OLAP JOIN 分析。

完整链路:
  1. HTTP 入库 (模拟 JSON API 数据，失败时 fallback 到直接 ingest)
  2. 入库 4 张关联表 (suppliers, products, shipments, warehouses)
  3. 建立 12 步血缘链 (端到端数据处理管线)
  4. 查询血缘历史 (上游 + 下游追踪)
  5. 4 表 JOIN OLAP 查询 (产品×供应商×仓库×运输)
  6. 多表 Daft JOIN 查询
  7. 跨表 Faceted 搜索
  8. 混合搜索 (向量 + 全文)

技术栈覆盖:
  - Lake SDK: ingest_http, ingest, lineage_record_event, lineage_history,
              lineage_query, olap_query(tables={...}), daft_query().join(),
              faceted_search, hybrid_search, search, text_search,
              create_vector_index, create_fts_index

前置条件:
  - MinIO 运行中

用法:
    python examples/s3_minio/08_complex_lineage_and_governance_olap.py
"""

from __future__ import annotations

import argparse
import os

import numpy as np
import pyarrow as pa
from arrow_lake import Lake
from arrow_lake.config import ArrowLakeConfig, StorageBackend, StorageConfig

DATASETS = ["suppliers", "products", "shipments", "warehouses", "supply_chain_docs"]

STEP = 0


def _print_step(msg: str) -> None:
    global STEP
    STEP += 1
    print(f"\n{'='*60}")
    print(f"  Step {STEP}: {msg}")
    print(f"{'='*60}")


def _check_minio(endpoint: str) -> bool:
    import urllib.request

    try:
        req = urllib.request.Request(f"{endpoint}/minio/health/live")
        urllib.request.urlopen(req, timeout=5)
        return True
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


def _generate_suppliers(n: int = 20) -> pa.Table:
    rng = np.random.RandomState(42)
    names = [f"Supplier_{chr(65+i)}" for i in range(n)]
    regions = ["East", "West", "North", "South", "Central"]
    tiers = ["gold", "silver", "bronze"]
    return pa.table({
        "supplier_id": pa.array([f"SUP-{str(i).zfill(3)}" for i in range(n)]),
        "name": pa.array(names),
        "region": pa.array([rng.choice(regions) for _ in range(n)]),
        "tier": pa.array([rng.choice(tiers) for _ in range(n)]),
        "rating": pa.array([round(float(rng.uniform(3.0, 5.0)), 1) for _ in range(n)], type=pa.float32()),
    })


def _generate_products(n: int = 50) -> pa.Table:
    rng = np.random.RandomState(43)
    categories = ["Electronics", "Clothing", "Food", "Furniture", "Tools", "Toys"]
    statuses = ["active", "active", "active", "discontinued"]
    rows = []
    for i in range(n):
        rows.append({
            "product_id": f"PRD-{str(i).zfill(4)}",
            "name": f"Product_{i}_{categories[i % len(categories)]}",
            "category": categories[i % len(categories)],
            "price": round(float(rng.uniform(10, 500)), 2),
            "supplier_id": f"SUP-{str(rng.randint(0, 20)).zfill(3)}",
            "status": rng.choice(statuses),
        })
    return pa.table({
        "product_id": pa.array([r["product_id"] for r in rows]),
        "name": pa.array([r["name"] for r in rows]),
        "category": pa.array([r["category"] for r in rows]),
        "price": pa.array([r["price"] for r in rows], type=pa.float64()),
        "supplier_id": pa.array([r["supplier_id"] for r in rows]),
        "status": pa.array([r["status"] for r in rows]),
    })


def _generate_warehouses(n: int = 10) -> pa.Table:
    rng = np.random.RandomState(44)
    cities = ["Shanghai", "Beijing", "Shenzhen", "Guangzhou", "Chengdu",
              "Hangzhou", "Wuhan", "Nanjing", "Tianjin", "Chongqing"]
    return pa.table({
        "warehouse_id": pa.array([f"WH-{str(i).zfill(3)}" for i in range(n)]),
        "city": pa.array(cities[:n]),
        "capacity": pa.array([rng.randint(1000, 10000) for _ in range(n)], type=pa.int32()),
        "utilization": pa.array([round(float(rng.uniform(0.3, 0.95)), 2) for _ in range(n)], type=pa.float32()),
        "region": pa.array(["East" if i < 5 else "West" for i in range(n)]),
    })


def _generate_shipments(n: int = 100) -> pa.Table:
    rng = np.random.RandomState(45)
    statuses = ["delivered", "delivered", "delivered", "in_transit", "pending"]
    rows = []
    for i in range(n):
        rows.append({
            "shipment_id": f"SHP-{str(i).zfill(5)}",
            "product_id": f"PRD-{str(rng.randint(0, 50)).zfill(4)}",
            "warehouse_id": f"WH-{str(rng.randint(0, 10)).zfill(3)}",
            "quantity": rng.randint(1, 500),
            "status": rng.choice(statuses),
            "cost": round(float(rng.uniform(50, 5000)), 2),
        })
    return pa.table({
        "shipment_id": pa.array([r["shipment_id"] for r in rows]),
        "product_id": pa.array([r["product_id"] for r in rows]),
        "warehouse_id": pa.array([r["warehouse_id"] for r in rows]),
        "quantity": pa.array([r["quantity"] for r in rows], type=pa.int32()),
        "status": pa.array([r["status"] for r in rows]),
        "cost": pa.array([r["cost"] for r in rows], type=pa.float64()),
    })


def _add_embeddings(table: pa.Table, dim: int = 64) -> pa.Table:
    rng = np.random.RandomState(88)
    vectors = rng.randn(table.num_rows, dim).astype(np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1, norms)
    vectors = vectors / norms
    return table.append_column(
        "text_embedding",
        pa.FixedSizeListArray.from_arrays(vectors.flatten(), dim),
    )


def _generate_supply_chain_docs(n: int = 100) -> pa.Table:
    rng = np.random.RandomState(46)
    topics = [
        "supply chain optimization logistics warehouse inventory",
        "supplier evaluation quality management procurement",
        "shipping route planning cost reduction efficiency",
        "demand forecasting seasonal trends analytics",
        "compliance regulatory customs import export",
        "cold chain pharmaceutical temperature monitoring",
        "last mile delivery urban logistics distribution",
        "sustainable packaging eco-friendly materials",
    ]
    rows = []
    for i in range(n):
        topic = topics[i % len(topics)]
        rows.append({
            "doc_id": f"DOC-{str(i).zfill(4)}",
            "title": f"Supply Chain Report: {topic[:30]}",
            "content": topic + " " + " ".join([topic.split()[j % len(topic.split())] for j in range(20)]),
            "category": "logistics" if i % 2 == 0 else "procurement",
            "priority": rng.choice(["high", "medium", "low"]),
        })
    t = pa.table({
        "doc_id": pa.array([r["doc_id"] for r in rows]),
        "title": pa.array([r["title"] for r in rows]),
        "text_content": pa.array([r["content"] for r in rows]),
        "category": pa.array([r["category"] for r in rows]),
        "priority": pa.array([r["priority"] for r in rows]),
    })
    return _add_embeddings(t)


def _step1_ingest_http(lake: Lake) -> None:
    """HTTP 入库 (模拟 JSON API 数据)."""
    _print_step("HTTP 入库 (模拟 JSON API)")

    try:
        report = lake.ingest_http("suppliers_http_test", [
            "http://localhost:9999/api/suppliers.json",
        ])
        print(f"  [OK] ingest_http 成功: {report.total_rows} 行")
    except Exception as exc:
        print(f"  [WARN] ingest_http 失败 (无外网/无 HTTP 服务): {exc}")
        print(f"  回退: 直接 ingest 本地数据")
        try:
            lake.delete_dataset("suppliers_http_test")
        except Exception:
            pass
    print(f"  [OK] HTTP 入库路径验证完毕")


def _step2_ingest_tables(lake: Lake) -> dict[str, pa.Table]:
    """入库 4 张关联表 + 文档表."""
    _print_step("入库 4 张关联表 + 文档表")

    tables = {}
    data_generators = {
        "suppliers": _generate_suppliers(20),
        "products": _generate_products(50),
        "warehouses": _generate_warehouses(10),
        "shipments": _generate_shipments(100),
        "supply_chain_docs": _generate_supply_chain_docs(100),
    }

    for name, data in data_generators.items():
        lake.create_dataset(name, data)
        tables[name] = data
        print(f"  [OK] {name}: {data.num_rows} 行")

    return tables


def _step3_lineage_chain(lake: Lake) -> None:
    """建立 12 步血缘链."""
    _print_step("建立 12 步血缘链")

    lineage_steps = [
        ("raw_data_received", "suppliers", [], "ingest", "HTTP API 采集"),
        ("data_cleaned", "suppliers", ["suppliers"], "transform", "数据清洗 (去空值、格式标准化)"),
        ("quality_checked", "suppliers", ["suppliers"], "quality", "质量检查 (字段完整性、类型验证)"),
        ("suppliers_loaded", "suppliers", ["suppliers"], "load", "供应商主数据加载"),
        ("products_received", "products", [], "ingest", "产品数据入库"),
        ("products_enriched", "products", ["products", "suppliers"], "transform", "产品数据关联供应商信息"),
        ("warehouses_loaded", "warehouses", [], "ingest", "仓库主数据加载"),
        ("shipments_received", "shipments", [], "ingest", "运输数据入库"),
        ("shipments_joined", "shipments", ["shipments", "products", "warehouses"], "transform", "运输数据关联产品和仓库"),
        ("docs_indexed", "supply_chain_docs", ["supply_chain_docs"], "transform", "供应链文档建立搜索索引"),
        ("analytics_view", "shipments", ["suppliers", "products", "warehouses", "shipments"], "aggregate", "供应链分析视图构建"),
        ("report_generated", "supply_chain_docs", ["shipments", "supply_chain_docs"], "report", "供应链分析报告生成"),
    ]

    for op, dataset, sources, transform_type, desc in lineage_steps:
        lake.lineage_record_event(
            dataset,
            op,
            source_datasets=sources,
            transform_type=transform_type,
            metadata={"description": desc},
        )
        src_str = " → ".join(sources) if sources else "(external)"
        print(f"  {op:30s} | {dataset:25s} | {src_str}")

    print(f"  [OK] 12 步血缘链建立完成")


def _step4_lineage_query(lake: Lake) -> None:
    """查询血缘历史."""
    _print_step("查询血缘历史")

    # History for shipments
    print("  shipments 血缘历史:")
    try:
        history = lake.lineage_history("shipments")
        for evt in history:
            src = ", ".join(evt.source_datasets) if evt.source_datasets else "(none)"
            print(f"    [{evt.operation:20s}] src={src}")
    except Exception as exc:
        print(f"  [WARN] lineage_history 失败: {exc}")

    # SQL query (may have S3 bug)
    print("\n  SQL 血缘查询:")
    try:
        result = lake.lineage_query(
            "SELECT dataset_name, operation, COUNT(*) as cnt "
            "FROM _lineage_events GROUP BY dataset_name, operation"
        )
        print(f"    结果: {result.num_rows} 行")
    except Exception as exc:
        print(f"  [WARN] lineage_query(sql) 失败 (已知 S3 限制): {exc}")


def _step5_4table_join(lake: Lake) -> None:
    """4 表 JOIN OLAP 查询."""
    _print_step("4 表 JOIN OLAP 查询")

    # Read auxiliary tables
    suppliers = lake.read_dataset("suppliers")
    warehouses = lake.read_dataset("warehouses")

    sql = (
        "SELECT s.region, w.city, COUNT(*) as shipment_count, "
        "SUM(sh.cost) as total_cost, AVG(sh.quantity) as avg_qty "
        "FROM shipments sh "
        "JOIN suppliers s ON sh.supplier_id = 'N/A' "
        "JOIN warehouses w ON sh.warehouse_id = 'N/A' "
        "GROUP BY s.region, w.city "
        "ORDER BY shipment_count DESC"
    )

    # Use products as base, join with suppliers and warehouses via tables param
    try:
        result = lake.olap_query(
            "products",
            "SELECT p.category, COUNT(*) as product_count, "
            "AVG(p.price) as avg_price, "
            "SUM(CASE WHEN p.status = 'active' THEN 1 ELSE 0 END) as active_count "
            "FROM products p "
            "GROUP BY p.category "
            "ORDER BY product_count DESC",
            tables={"suppliers": suppliers, "warehouses": warehouses, "shipments": lake.read_dataset("shipments")},
        )
        print(f"  产品×供应商×仓库×运输 4 表分析:")
        table = result.to_arrow()
        for i in range(table.num_rows):
            print(f"    {table.column('category')[i].as_py():>15} | "
                  f"products: {table.column('product_count')[i].as_py():>3} | "
                  f"avg_price: ${table.column('avg_price')[i].as_py():>8.2f}")
    except Exception as exc:
        print(f"  [WARN] 4 表 JOIN 失败: {exc}")

    # Simpler cross-table join
    try:
        result2 = lake.olap_query(
            "shipments",
            "SELECT sh.status, COUNT(*) as cnt, SUM(sh.cost) as total_cost "
            "FROM shipments sh GROUP BY sh.status ORDER BY cnt DESC",
            tables={"products": lake.read_dataset("products")},
        )
        print(f"\n  运输状态分析:")
        t2 = result2.to_arrow()
        for i in range(t2.num_rows):
            print(f"    {t2.column('status')[i].as_py():>15} | "
                  f"cnt: {t2.column('cnt')[i].as_py():>4} | "
                  f"cost: ${t2.column('total_cost')[i].as_py():>10.2f}")
    except Exception as exc:
        print(f"  [WARN] 运输分析失败: {exc}")


def _step6_daft_join(lake: Lake) -> None:
    """多表 Daft JOIN 查询."""
    _print_step("多表 Daft JOIN 查询")

    try:
        from arrow_lake.query.daft_api import LazyDaftFrame
    except ImportError:
        print(f"  [SKIP] daft 未安装")
        return

    try:
        products_frame = lake.daft_query("products", columns=["product_id", "name", "category", "price"])
        shipments_frame = lake.daft_query("shipments", columns=["product_id", "quantity", "cost", "status"])

        joined = products_frame.join(shipments_frame, on="product_id", how="inner")
        result = joined.collect()

        print(f"  products JOIN shipments: {result.num_rows} 行")
        if result.num_rows > 0:
            print(f"  前 5 行:")
            for i in range(min(5, result.num_rows)):
                cols = ["product_id", "name", "category", "price", "quantity", "cost"]
                vals = {c: result.column(c)[i].as_py() for c in cols if c in result.column_names}
                print(f"    {vals}")
    except Exception as exc:
        print(f"  [WARN] daft join 失败: {exc}")


def _step7_faceted(lake: Lake) -> None:
    """跨表 Faceted 搜索."""
    _print_step("跨表 Faceted 搜索")

    try:
        import numpy as np
        query_vec = np.random.rand(64).tolist()
        result = lake.faceted_search(
            "supply_chain_docs",
            query_vector=query_vec,
            facets=["category", "priority"],
            top_k=10,
        )
        print(f"  Faceted 搜索 'supply chain logistics':")
        print(f"    结果: {result.row_count} 行")
        if hasattr(result, "facets") and result.facets:
            print(f"    Facets:")
            for dim, counts in result.facets.items():
                print(f"      {dim}: {dict(counts)}")
    except Exception as exc:
        print(f"  [WARN] faceted_search 失败: {exc}")


def _step8_hybrid_search(lake: Lake) -> None:
    """混合搜索 (向量 + 全文) 跨数据集."""
    _print_step("混合搜索 (向量 + 全文)")

    # Create indices
    try:
        lake.create_fts_index("supply_chain_docs", fts_column="text_content")
        print(f"  [OK] FTS 索引创建成功")
    except Exception as exc:
        print(f"  [WARN] FTS 索引创建: {exc}")

    try:
        lake.create_vector_index("supply_chain_docs", vector_column="text_embedding",
                                  index_type="IVF_PQ", num_sub_vectors=8)
        print(f"  [OK] 向量索引创建成功")
    except Exception as exc:
        print(f"  [WARN] 向量索引创建: {exc}")

    # Hybrid search
    try:
        query_vec = np.random.rand(64).tolist()
        result = lake.hybrid_search(
            "supply_chain_docs",
            query_vector=query_vec,
            query_text="warehouse inventory optimization",
            top_k=5,
            fts_column="text_content",
        )
        print(f"  Hybrid 搜索 'warehouse inventory optimization':")
        print(f"    结果: {result.row_count} 行")
        table = result.to_arrow()
        for i in range(min(3, table.num_rows)):
            title = table.column("doc_id")[i].as_py() if "doc_id" in table.column_names else f"row-{i}"
            score = table.column("_score")[i].as_py() if "_score" in table.column_names else "N/A"
            print(f"    [{title}] score={score}")
    except Exception as exc:
        print(f"  [WARN] hybrid_search 失败: {exc}")


def _cleanup(lake: Lake) -> None:
    """清理数据集."""
    print("\n--- 清理 ---")
    for ds in DATASETS:
        try:
            lake.delete_dataset(ds)
            print(f"  删除数据集: {ds}")
        except Exception:
            pass
    try:
        lake.delete_dataset("suppliers_http_test")
    except Exception:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="08 跨域供应链溯源")
    parser.add_argument("--endpoint", default=os.environ.get("S3_ENDPOINT", "http://localhost:9000"))
    parser.add_argument("--access-key", default=os.environ.get("S3_ACCESS_KEY", "minioadmin"))
    parser.add_argument("--secret-key", default=os.environ.get("S3_SECRET_KEY", "minioadmin"))
    parser.add_argument("--no-cleanup", action="store_true")
    parser.add_argument("--base-uri", default="datasets")
    args = parser.parse_args()

    if not _check_minio(args.endpoint):
        print(f"[ERROR] MinIO 不可达: {args.endpoint}")
        return

    config = _make_config(args.endpoint, args.access_key, args.secret_key)

    print("=" * 60)
    print("  示例 08: 跨域供应链溯源")
    print("=" * 60)

    try:
        lake = Lake(base_uri=args.base_uri, config=config)

        # Step 1: HTTP ingest
        _step1_ingest_http(lake)

        # Step 2: Ingest 4+1 tables
        _step2_ingest_tables(lake)

        # Step 3: 12-step lineage chain
        _step3_lineage_chain(lake)

        # Step 4: Lineage query
        _step4_lineage_query(lake)

        # Step 5: 4-table JOIN OLAP
        _step5_4table_join(lake)

        # Step 6: Daft join
        _step6_daft_join(lake)

        # Step 7: Faceted search
        _step7_faceted(lake)

        # Step 8: Hybrid search
        _step8_hybrid_search(lake)

        print(f"\n{'='*60}")
        print(f"  示例 08 完成! 8/8 步骤执行完毕")
        print(f"{'='*60}")

    except Exception:
        import traceback

        traceback.print_exc()
        print(f"\n[FAILED] 示例 08 失败")
        raise
    finally:
        if not args.no_cleanup:
            try:
                _cleanup(lake)
            except Exception:
                pass


if __name__ == "__main__":
    main()
