"""v1.10.2 M1 E2E: 文本增量摄入 → embedding null 回填 → 向量检索命中新行.

验证 P1 核心场景(host facade + 真 ollama embedding,qwen3-embedding:4b/2560维):
  1. create_dataset(A 文本)      — 首次"摄入",无 text_embedding 列
  2. embed_and_add                — 首次全量 encode(走首次路径)
  3. append_dataset(B 文本)       — 增量 append;B 行 text_embedding = null
                                    (_evolve_and_align_schema 反向 back-fill NULL)
  4. embed_and_add                — P1 核心: _backfill_embedding_nulls 只回填 null 行
  5. 断言 A 向量逐位不变 + B 向量非 null
  6. 向量 query B 的关键词 → top-1 命中 B(回填后新行可被检索)

运行:
  .venv/bin/python3 tests/e2e/test_embedding_backfill_e2e.py
  .venv/bin/python3 -m pytest tests/e2e/test_embedding_backfill_e2e.py -q

依赖: ollama 127.0.0.1:11434 + qwen3-embedding:4b
"""
from __future__ import annotations

import os
import tempfile

# 必须在 import arrow_lake 之前设置 —— 配置在 import 时从 env 读取
os.environ.setdefault("ARROW_LAKE__EMBEDDING__BACKEND", "openai")
os.environ.setdefault("ARROW_LAKE__EMBEDDING__API_BASE", "http://127.0.0.1:11434/v1")
os.environ.setdefault("ARROW_LAKE__EMBEDDING__MODEL", "qwen3-embedding:4b")
os.environ.setdefault("ARROW_LAKE__EMBEDDING__API_KEY", "ollama")
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost,host.docker.internal")

import pyarrow as pa  # noqa: E402

from arrow_lake import Lake  # noqa: E402
from arrow_lake.config import ArrowLakeConfig, StorageConfig  # noqa: E402

DS = "e2e_incr_backfill"
# A / B 语义相距远:自然语言 vs 量子密码学,确保 query 命中 B 不命中 A
A_TEXT = "The quick brown fox jumps over the lazy dog near the quiet riverbank at dawn."
B_TEXT = (
    "Quantum entanglement enables provably secure cryptographic key "
    "distribution between two distant parties over a noisy channel."
)
QUERY_TEXT = "quantum cryptography secure key distribution"


def _make_lake() -> tuple[Lake, str]:
    base = tempfile.mkdtemp(prefix="e2e_backfill_")
    cfg = ArrowLakeConfig(storage=StorageConfig(backend="local"))
    return Lake(base_uri=base, config=cfg), base


def _emb_column(lake: Lake) -> list:
    """Read the text_embedding column as a python list (None for null rows)."""
    storage = lake._get_storage()
    return (
        storage.read_dataset(DS, columns=["text_embedding"])
        .column("text_embedding")
        .to_pylist()
    )


def run_e2e() -> dict:
    lake, base = _make_lake()
    storage = lake._get_storage()
    report: dict = {"base": base}

    # 1. 首次"摄入" A(仅 text_content,无 text_embedding 列)
    storage.create_dataset(
        DS, pa.table({"document_id": ["a1"], "text_content": pa.array([A_TEXT])})
    )

    # 2. 首次 embed_and_add → 全量 encode(首次路径:无 text_embedding 列)
    n1 = lake.embed_and_add(DS)
    assert n1 == 1, f"first embed should add 1 row, got {n1}"
    report["first_embed_rows"] = n1

    # 快照 A 向量 —— 回填后必须逐位不变
    a_vec_before = _emb_column(lake)[0]
    assert a_vec_before is not None, "A embedding must exist after first embed"
    report["a_vec_dim"] = len(a_vec_before)

    # 3. 增量 append B(text_content=B;无 text_embedding 列 → 对齐后 B 行该列 null)
    storage.append_dataset(
        DS, pa.table({"document_id": ["b1"], "text_content": pa.array([B_TEXT])})
    )
    embs = _emb_column(lake)
    assert embs[1] is None, (
        f"B embedding must be NULL right after append (got {type(embs[1]).__name__})"
    )
    report["b_null_after_append"] = True

    # 4. P1 核心: embed_and_add 检测到列已存在 → 走 _backfill_embedding_nulls
    n2 = lake.embed_and_add(DS)
    assert n2 == 1, f"backfill should fill exactly 1 null row, got {n2}"
    report["backfilled_rows"] = n2

    # 5. A 向量逐位不变 + B 向量已填
    embs_after = _emb_column(lake)
    a_vec_after, b_vec = embs_after[0], embs_after[1]
    assert b_vec is not None, "B embedding must be non-null after backfill"
    assert a_vec_after == a_vec_before, (
        "A embedding must be bit-identical after backfill (only null rows touched)"
    )
    report["a_preserved"] = True
    report["b_filled_dim"] = len(b_vec)

    # 6. 向量检索 B 关键词 → top-1 命中 B(2 行 flat scan,无需 IVF 索引)
    qvecs, _dim = lake._encode_texts([QUERY_TEXT], lake._config.embedding)
    result = lake.search(DS, qvecs[0].tolist(), top_k=2, metric="cosine")
    top_ids = result.table.column("document_id").to_pylist()
    distances = result.table.column("_distance").to_pylist()
    assert top_ids[0] == "b1", (
        f"query should rank B (b1) top-1, got order={top_ids}"
    )
    report["search_top1"] = top_ids[0]
    report["search_order"] = top_ids
    report["search_distances"] = [round(float(d), 4) for d in distances]
    return report


import pytest


def test_embedding_backfill_e2e() -> None:
    """pytest entry: incremental embedding backfill end-to-end (P1).

    Requires a LIVE embedding endpoint (the configured embedding API base);
    skipped when it answers non-200 (e.g. relay down) so the default suite
    stays hermetic.
    """
    import os
    import urllib.request

    from arrow_lake.config import ArrowLakeConfig

    base = ArrowLakeConfig().embedding.api_base
    try:
        req = urllib.request.Request(base.rstrip("/") + "/models")
        with urllib.request.urlopen(req, timeout=3) as resp:
            if resp.status != 200:
                raise OSError(f"status {resp.status}")
    except Exception:
        pytest.skip(f"embedding endpoint unavailable at {base} — live-stack e2e")

    report = run_e2e()
    assert report["a_preserved"]
    assert report["search_top1"] == "b1"


if __name__ == "__main__":
    r = run_e2e()
    base = r.pop("base")
    print("\n=== v1.10.2 M1 E2E PASS ===")
    for k, v in r.items():
        print(f"  {k}: {v}")
    print(f"  tmp_base: {base}")
