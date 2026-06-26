"""Batch-3 gate benchmarks — v1.8.0 🟨 压测驱动决策框架.

Produces the data that gates the three batch-3 decisions:

- **#17 全链路 async**: ThreadPool 并发查询吞吐随 worker 数的平台期检测。
  若 QPS 不随 worker 近线性增长 → 存在 GIL/连接池争用 → async 有理由。
  （对照 test_scale_single_node 的固定 10/20 worker 测量，这里做平台期 sweep。）
- **#15 分布式索引**: create_index 构建时长随数据规模，推断单节点天花板。
  现有 bench 测 ingest/search 延迟，缺索引 **构建** 时长 → backfill 决策依据。
- **#7 ColBERT/colpali**: 当前单向量 ANN recall@k 基线 vs 簇结构 ground truth。
  现有 test_bench_quality 测的是 QualityFilter（非召回）→ 召回缺口决策依据。

Run: ``.venv/bin/pytest tests/benchmark/test_bench_batch3_gates.py -m benchmark -s``
（``-s`` 看 gate verdict 打印；``-m benchmark`` 跳过默认 CI）。
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pyarrow as pa
import pytest

from tests.benchmark.benchmark_report import BenchmarkReport


def _make_vector_table(n: int, dim: int = 128, seed: int = 42) -> pa.Table:
    """Random L2-normalized vectors (uniform on unit sphere)."""
    rng = np.random.RandomState(seed)
    vectors = rng.randn(n, dim).astype(np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1, norms)
    vectors = vectors / norms
    return pa.table(
        {
            "id": [f"doc_{i:06d}" for i in range(n)],
            "text_content": [f"document {i}" for i in range(n)],
            "text_embedding": pa.FixedSizeListArray.from_arrays(vectors.ravel(), dim),
        }
    )


def _make_clustered_table(
    n_clusters: int = 20,
    per_cluster: int = 50,
    dim: int = 64,
    seed: int = 7,
    noise: float = 0.1,
) -> tuple[pa.Table, np.ndarray, int]:
    """Clustered vectors + their centroids.

    Ground truth for centroid ``ci`` = its ``per_cluster`` members, so
    recall@per_cluster of a centroid query measures ANN retrieval quality
    against a known-correct neighborhood.
    """
    rng = np.random.RandomState(seed)
    centroids = rng.randn(n_clusters, dim).astype(np.float32)
    centroids /= np.linalg.norm(centroids, axis=1, keepdims=True)
    vecs: list[np.ndarray] = []
    labels: list[int] = []
    for ci, c in enumerate(centroids):
        pts = c + noise * rng.randn(per_cluster, dim).astype(np.float32)
        vecs.append(pts)
        labels.extend([ci] * per_cluster)
    X = np.vstack(vecs).astype(np.float32)
    X /= np.linalg.norm(X, axis=1, keepdims=True)
    table = pa.table(
        {
            "id": [f"doc_{i:06d}" for i in range(len(X))],
            "cluster": pa.array(labels, type=pa.int32()),
            "text_embedding": pa.FixedSizeListArray.from_arrays(X.ravel(), dim),
        }
    )
    return table, centroids, per_cluster


# ---------------------------------------------------------------------------
# #17 — async gate: concurrent query throughput plateau detection
# ---------------------------------------------------------------------------


@pytest.mark.benchmark
class TestBatch3ConcurrencyGate:
    """#17 gate: 并发查询吞吐平台期 → 是否需要 async。"""

    @pytest.mark.parametrize("workers", [1, 5, 10, 20])
    def test_concurrent_query_qps(self, workers: int, lance_tmp_dir: str) -> None:
        from arrow_lake.ingest.storage import LanceStorageManager
        from arrow_lake.query.vector import VectorSearchBridge

        storage = LanceStorageManager(base_uri=lance_tmp_dir)
        storage.create_dataset("gate_conc", _make_vector_table(10_000))
        bridge = VectorSearchBridge(storage)
        bridge.create_index("gate_conc", vector_column="text_embedding", num_sub_vectors=16)

        rng = np.random.RandomState(99)
        queries = [
            (rng.randn(128).astype(np.float32) / np.linalg.norm(rng.randn(128))).tolist()
            for _ in range(100)
        ]

        def _query(i: int) -> None:
            bridge.search("gate_conc", queries[i], top_k=10, vector_column="text_embedding")

        _query(0)  # warmup

        with ThreadPoolExecutor(max_workers=workers) as ex:
            t0 = time.perf_counter()
            list(ex.map(_query, range(100)))
            elapsed = time.perf_counter() - t0

        qps = 100 / elapsed if elapsed > 0 else 0.0
        print(f"\n[#17 gate] workers={workers:2d}  →  {qps:6.1f} QPS  ({elapsed:.2f}s)")
        assert qps > 0


# ---------------------------------------------------------------------------
# #15 — distributed-index gate: create_index build time at scale
# ---------------------------------------------------------------------------


@pytest.mark.benchmark
class TestBatch3IndexBuildScaleGate:
    """#15 gate: 索引构建时长 × 规模 → 单节点天花板（backfill 决策）。"""

    @pytest.mark.parametrize("n", [10_000, 100_000, 1_000_000])
    def test_index_build_time_at_scale(self, n: int, lance_tmp_dir: str) -> None:
        from arrow_lake.ingest.storage import LanceStorageManager
        from arrow_lake.query.vector import VectorSearchBridge

        storage = LanceStorageManager(base_uri=lance_tmp_dir)
        ds = f"gate_idx_{n}"
        storage.create_dataset(ds, _make_vector_table(n))
        bridge = VectorSearchBridge(storage)

        report = BenchmarkReport(f"batch3_index_build_{n}")
        elapsed = report.measure(
            f"create_index IVF_PQ ({n:,} rows, dim=128)",
            lambda: bridge.create_index(ds, vector_column="text_embedding", num_sub_vectors=16),
            rows=n,
            repeats=1,
        )
        report.print_summary()

        # Search latency at scale (p50/p95 over 20 queries)
        rng = np.random.RandomState(123)
        q = rng.randn(128).astype(np.float32)
        q = q / np.linalg.norm(q)
        bridge.search(ds, q.tolist(), top_k=10, vector_column="text_embedding")  # warmup
        stimes: list[float] = []
        for _ in range(20):
            t0 = time.perf_counter()
            bridge.search(ds, q.tolist(), top_k=10, vector_column="text_embedding")
            stimes.append(time.perf_counter() - t0)
        stimes.sort()
        p50_ms = stimes[len(stimes) // 2] * 1000
        p95_ms = stimes[int(len(stimes) * 0.95)] * 1000

        print(
            f"\n[#15 gate] n={n:>9,}  build={elapsed:6.1f}s  "
            f"search p50={p50_ms:5.1f}ms p95={p95_ms:5.1f}ms"
        )
        assert elapsed > 0


# ---------------------------------------------------------------------------
# #7 — ColBERT gate: ANN recall@k baseline vs clustered ground truth
# ---------------------------------------------------------------------------


@pytest.mark.benchmark
class TestBatch3RecallGate:
    """#7 gate: 当前单向量 ANN recall@k 基线 → 召回缺口（ColBERT 决策）。"""

    def test_recall_at_k_indexed_ann(self, lance_tmp_dir: str) -> None:
        from arrow_lake.ingest.storage import LanceStorageManager
        from arrow_lake.query.vector import VectorSearchBridge

        n_clusters, per_cluster, dim = 20, 50, 64
        table, centroids, k = _make_clustered_table(n_clusters, per_cluster, dim)

        def _recall_for(bridge: VectorSearchBridge, ds: str) -> float:
            recalls: list[float] = []
            for ci, c in enumerate(centroids):
                res = bridge.search(ds, c.tolist(), top_k=k, vector_column="text_embedding")
                retrieved = res.table.column("cluster").to_pylist()
                hits = sum(1 for cl in retrieved if cl == ci)
                recalls.append(hits / k)
            return sum(recalls) / len(recalls)

        # Indexed ANN
        storage = LanceStorageManager(base_uri=lance_tmp_dir)
        storage.create_dataset("gate_recall", table)
        bridge = VectorSearchBridge(storage)
        bridge.create_index("gate_recall", vector_column="text_embedding", num_sub_vectors=16)
        recall_indexed = _recall_for(bridge, "gate_recall")

        # Brute-force baseline (no index)
        storage2 = LanceStorageManager(base_uri=lance_tmp_dir + "_bf")
        storage2.create_dataset("gate_recall_bf", table)
        bridge_bf = VectorSearchBridge(storage2)
        recall_bruteforce = _recall_for(bridge_bf, "gate_recall_bf")

        retention = recall_indexed / max(recall_bruteforce, 1e-9)
        print(
            f"\n[#7 gate] recall@{k} (n={n_clusters}×{per_cluster}={n_clusters*per_cluster}, "
            f"dim={dim})\n"
            f"          indexed ANN   : {recall_indexed:.3f}\n"
            f"          brute-force   : {recall_bruteforce:.3f}\n"
            f"          ANN/brute retention: {retention:.1%}"
        )
        assert 0.0 <= recall_indexed <= 1.0
        assert 0.0 <= recall_bruteforce <= 1.0

    @pytest.mark.parametrize(
        ("n_clusters", "per_cluster", "noise"),
        [(20, 50, 0.10), (100, 50, 0.30), (200, 30, 0.50)],
        ids=["easy", "medium", "hard"],
    )
    def test_recall_difficulty_sweep(
        self, n_clusters: int, per_cluster: int, noise: float, lance_tmp_dir: str
    ) -> None:
        """Recall@10 ANN vs brute across difficulty — finds where ANN degrades.

        Easy (well-separated) → ANN ≈ brute. Hard (overlapping) → ANN loses
        recall vs brute (IVF_PQ quantization). The curve gates #7: a deficit
        here is an index-quality issue (fix with HNSW / higher nprobes), not
        a ColBERT use case (ColBERT addresses fine-grained text token match).
        """
        from arrow_lake.ingest.storage import LanceStorageManager
        from arrow_lake.query.vector import VectorSearchBridge

        dim, k = 128, 10
        table, centroids, _ = _make_clustered_table(
            n_clusters, per_cluster, dim=dim, noise=noise
        )

        storage = LanceStorageManager(base_uri=lance_tmp_dir + f"_{int(noise * 100)}")
        ds = "gate_sweep"
        storage.create_dataset(ds, table)
        bridge = VectorSearchBridge(storage)
        bridge.create_index(ds, vector_column="text_embedding", num_sub_vectors=16)
        ds_bf = ds + "_bf"
        storage.create_dataset(ds_bf, table)

        def _recall(b: VectorSearchBridge, name: str) -> float:
            recs: list[float] = []
            for ci, c in enumerate(centroids):
                r = b.search(name, c.tolist(), top_k=k, vector_column="text_embedding")
                recs.append(
                    sum(1 for cl in r.table.column("cluster").to_pylist() if cl == ci) / k
                )
            return sum(recs) / len(recs)

        ann = _recall(bridge, ds)
        bf = _recall(bridge, ds_bf)
        print(
            f"\n[#7 sweep] clusters={n_clusters:>3} noise={noise:.2f} "
            f"(n={n_clusters * per_cluster:,}): ANN@{k}={ann:.3f} brute@{k}={bf:.3f} "
            f"retention={ann / max(bf, 1e-9):.1%}"
        )
        assert 0.0 <= ann <= 1.0
